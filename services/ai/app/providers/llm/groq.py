"""
Groq — hosted LLM on a free tier, OpenAI-compatible API.

Written against the documented API but NOT yet verified end to end: no key is
configured. Set GROQ_API_KEY in .env and LLM_PROVIDER=groq to exercise it.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from app.config import settings
from app.providers.base import LLMResponse, Message, ProviderError, ToolCall, ToolSpec

API_URL = "https://api.groq.com/openai/v1/chat/completions"


def _to_wire(message: Message) -> dict[str, Any]:
    if message.role == "tool":
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "content": message.content or "",
        }

    payload: dict[str, Any] = {"role": message.role, "content": message.content or ""}
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": c.id,
                "type": "function",
                # The OpenAI shape carries arguments as a JSON *string*.
                "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
            }
            for c in message.tool_calls
        ]
    return payload


class GroqLLM:
    name = "groq"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key or settings.groq_api_key
        self.model = model or settings.groq_llm_model

    async def complete(
        self, messages: list[Message], tools: list[ToolSpec] | None = None
    ) -> LLMResponse:
        if not self.api_key:
            raise ProviderError(
                self.name,
                "GROQ_API_KEY is not set — get a free key at https://console.groq.com/keys",
                retryable=False, code="MISSING_API_KEY",
            )

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [_to_wire(m) for m in messages],
            "temperature": 0.3,
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
            payload["tool_choice"] = "auto"

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=settings.llm_timeout_sec) as client:
                response = await client.post(
                    API_URL,
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                self.name, f"Timed out after {settings.llm_timeout_sec}s",
                retryable=True, code="MODEL_TIMEOUT", cause=exc,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                self.name, f"Network error: {exc}", retryable=True,
                code="CONNECTION_FAILED", cause=exc,
            ) from exc

        # Free tiers rate-limit aggressively, so 429 is the expected failure
        # here rather than an exceptional one.
        if response.status_code == 429:
            raise ProviderError(
                self.name, "Rate limited by Groq", retryable=True, code="RATE_LIMITED"
            )
        if response.status_code in (401, 403):
            raise ProviderError(
                self.name, "Groq rejected the API key", retryable=False, code="UNAUTHORIZED"
            )
        if response.status_code >= 500:
            raise ProviderError(
                self.name, f"Groq returned {response.status_code}",
                retryable=True, code="SERVICE_UNAVAILABLE",
            )
        if response.status_code >= 400:
            raise ProviderError(
                self.name, f"Groq rejected the request: {response.text[:200]}",
                retryable=False, code="BAD_REQUEST",
            )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        body = response.json()
        choice = body["choices"][0]["message"]

        tool_calls: list[ToolCall] = []
        for entry in choice.get("tool_calls") or []:
            function = entry.get("function", {})
            raw_args = function.get("arguments") or "{}"
            try:
                arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                arguments = {}
            tool_calls.append(
                ToolCall(id=entry.get("id", ""), name=function.get("name", ""), arguments=arguments)
            )

        usage = body.get("usage", {})
        return LLMResponse(
            content=choice.get("content") or None,
            tool_calls=tool_calls,
            provider=self.name,
            model=self.model,
            duration_ms=elapsed_ms,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )
