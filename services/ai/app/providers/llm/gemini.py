"""
Google Gemini — hosted LLM on a free tier.

Written against the documented API but NOT yet verified end to end: no key is
configured. Set GEMINI_API_KEY in .env and LLM_PROVIDER=gemini to exercise it.

Gemini's wire format differs from OpenAI's in three ways that matter here:
roles are "user"/"model" (not "assistant"), the system prompt is a separate
top-level field, and tool results are parts rather than messages.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx

from app.config import settings
from app.providers.base import LLMResponse, Message, ProviderError, ToolCall, ToolSpec

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


def _to_contents(messages: list[Message]) -> tuple[list[dict[str, Any]], str | None]:
    """Convert our messages into Gemini `contents`, lifting out the system prompt."""
    system_prompt: str | None = None
    contents: list[dict[str, Any]] = []

    for message in messages:
        if message.role == "system":
            system_prompt = message.content
            continue

        if message.role == "tool":
            contents.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": message.name or "",
                                "response": {"result": message.content or ""},
                            }
                        }
                    ],
                }
            )
            continue

        parts: list[dict[str, Any]] = []
        if message.content:
            parts.append({"text": message.content})
        for call in message.tool_calls:
            parts.append({"functionCall": {"name": call.name, "args": call.arguments}})
        if not parts:
            continue

        contents.append(
            {"role": "model" if message.role == "assistant" else "user", "parts": parts}
        )

    return contents, system_prompt


class GeminiLLM:
    name = "gemini"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key or settings.gemini_api_key
        self.model = model or settings.gemini_model

    async def complete(
        self, messages: list[Message], tools: list[ToolSpec] | None = None
    ) -> LLMResponse:
        if not self.api_key:
            raise ProviderError(
                self.name,
                "GEMINI_API_KEY is not set — get a free key at https://aistudio.google.com/apikey",
                retryable=False, code="MISSING_API_KEY",
            )

        contents, system_prompt = _to_contents(messages)
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": 0.3},
        }
        if system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}
        if tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.parameters,
                        }
                        for t in tools
                    ]
                }
            ]

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=settings.llm_timeout_sec) as client:
                response = await client.post(
                    f"{BASE_URL}/{self.model}:generateContent",
                    json=payload,
                    headers={"x-goog-api-key": self.api_key},
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

        if response.status_code == 429:
            raise ProviderError(
                self.name, "Rate limited by Gemini", retryable=True, code="RATE_LIMITED"
            )
        if response.status_code in (401, 403):
            raise ProviderError(
                self.name, "Gemini rejected the API key", retryable=False, code="UNAUTHORIZED"
            )
        if response.status_code >= 500:
            raise ProviderError(
                self.name, f"Gemini returned {response.status_code}",
                retryable=True, code="SERVICE_UNAVAILABLE",
            )
        if response.status_code >= 400:
            raise ProviderError(
                self.name, f"Gemini rejected the request: {response.text[:200]}",
                retryable=False, code="BAD_REQUEST",
            )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        body = response.json()

        candidates = body.get("candidates") or []
        if not candidates:
            # Usually a safety block. Nothing to retry — the prompt produced no
            # candidate at all, and sending it again produces the same result.
            raise ProviderError(
                self.name, "Gemini returned no candidates (possibly a safety block)",
                retryable=False, code="NO_CANDIDATES",
            )

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for part in candidates[0].get("content", {}).get("parts", []):
            if "text" in part:
                text_parts.append(part["text"])
            elif "functionCall" in part:
                call = part["functionCall"]
                tool_calls.append(
                    ToolCall(
                        # Gemini does not issue call ids either.
                        id=f"call_{uuid.uuid4().hex[:12]}",
                        name=call.get("name", ""),
                        arguments=call.get("args") or {},
                    )
                )

        usage = body.get("usageMetadata", {})
        return LLMResponse(
            content="".join(text_parts) or None,
            tool_calls=tool_calls,
            provider=self.name,
            model=self.model,
            duration_ms=elapsed_ms,
            prompt_tokens=usage.get("promptTokenCount"),
            completion_tokens=usage.get("candidatesTokenCount"),
        )
