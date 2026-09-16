"""
Ollama — the local LLM provider.

Runs natively on the host so it gets Metal acceleration; a containerised Ollama
on macOS would be CPU-only (ADR 0001). Free, offline, and good enough at tool
calling with qwen2.5:7b-instruct, which is the assumption this whole phase rests
on and which we verified before writing any of it.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import httpx

from app.config import settings
from app.providers.base import LLMResponse, Message, ProviderError, ToolCall, ToolSpec


def _to_wire(message: Message) -> dict[str, Any]:
    payload: dict[str, Any] = {"role": message.role, "content": message.content or ""}

    if message.tool_calls:
        payload["tool_calls"] = [
            {"function": {"name": c.name, "arguments": c.arguments}} for c in message.tool_calls
        ]
    # Ollama identifies a tool result by name rather than by call id.
    if message.role == "tool" and message.name:
        payload["tool_name"] = message.name

    return payload


def _parse_tool_calls(raw: list[dict[str, Any]] | None) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for entry in raw or []:
        function = entry.get("function", {})
        arguments = function.get("arguments", {})
        # Ollama usually returns a decoded object, but some models emit a JSON
        # string. Accept both rather than failing the whole turn on a quirk.
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        calls.append(
            ToolCall(
                # Ollama does not issue call ids; the orchestrator needs one to
                # pair results with requests, so we mint it here.
                id=entry.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                name=function.get("name", ""),
                arguments=arguments if isinstance(arguments, dict) else {},
            )
        )
    return calls


class OllamaLLM:
    name = "ollama"

    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.model = model or settings.ollama_model

    async def complete(
        self, messages: list[Message], tools: list[ToolSpec] | None = None
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [_to_wire(m) for m in messages],
            "stream": False,
            # Deterministic enough to debug, varied enough to sound human.
            "options": {"temperature": 0.3},
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

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=settings.llm_timeout_sec) as client:
                response = await client.post(f"{self.base_url}/api/chat", json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderError(
                self.name, f"Timed out after {settings.llm_timeout_sec}s",
                retryable=True, code="MODEL_TIMEOUT", cause=exc,
            ) from exc
        except httpx.ConnectError as exc:
            raise ProviderError(
                self.name,
                f"Cannot reach Ollama at {self.base_url} — is it running? "
                "(`brew services start ollama`)",
                retryable=True, code="CONNECTION_FAILED", cause=exc,
            ) from exc

        if response.status_code >= 500:
            raise ProviderError(
                self.name, f"Ollama returned {response.status_code}",
                retryable=True, code="SERVICE_UNAVAILABLE",
            )
        if response.status_code == 404:
            # Almost always a model that was never pulled. Retrying will not
            # make it appear, so this is permanent and says what to do.
            raise ProviderError(
                self.name,
                f"Model '{self.model}' not found — run `ollama pull {self.model}`",
                retryable=False, code="MODEL_NOT_FOUND",
            )
        if response.status_code >= 400:
            raise ProviderError(
                self.name, f"Ollama rejected the request: {response.text[:200]}",
                retryable=False, code="BAD_REQUEST",
            )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        body = response.json()
        message = body.get("message", {})

        return LLMResponse(
            content=message.get("content") or None,
            tool_calls=_parse_tool_calls(message.get("tool_calls")),
            provider=self.name,
            model=self.model,
            duration_ms=elapsed_ms,
            prompt_tokens=body.get("prompt_eval_count"),
            completion_tokens=body.get("eval_count"),
        )
