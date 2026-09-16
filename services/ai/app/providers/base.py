"""
Provider interfaces.

Three swappable layers — speech in, reasoning, speech out — each with a local
implementation that runs free and offline on this machine, and a hosted one on a
free tier. Which is used is an environment variable, not a code change.

The abstraction is not speculative. Local models are cheap but slow and limited;
hosted models are fast and capable but rate-limited and occasionally down. A real
voice system needs to move between them, and the retry engine in Phase 6 needs a
uniform failure to react to. Defining that boundary here means neither the
orchestrator nor the retry engine ever learns a provider's name.

Every call returns its own elapsed time. Latency is the defining constraint of a
voice interface, so it is measured at the boundary rather than inferred later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

Role = Literal["system", "user", "assistant", "tool"]


# ---------- Shared failure type ----------


class ProviderError(Exception):
    """
    A provider failed.

    `retryable` carries the same meaning as in the flight service: transient
    conditions (timeout, 429, 5xx, a model still loading) are worth another
    attempt; a malformed request or a missing API key is not.
    """

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        retryable: bool,
        code: str = "PROVIDER_ERROR",
        cause: Exception | None = None,
    ) -> None:
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.message = message
        self.retryable = retryable
        self.code = code
        self.cause = cause


# ---------- Speech to text ----------


@dataclass(slots=True)
class Transcript:
    text: str
    provider: str
    duration_ms: int
    language: str | None = None
    # Overall confidence where the provider reports one. Phase 5 uses a low
    # value as a trigger to re-prompt or escalate rather than act on a guess.
    confidence: float | None = None


@runtime_checkable
class STTProvider(Protocol):
    name: str

    async def transcribe(self, audio: bytes, *, mime_type: str = "audio/wav") -> Transcript: ...


# ---------- Language model ----------


@dataclass(slots=True)
class ToolCall:
    """A tool the model asked us to run, with the arguments it chose."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class Message:
    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    # Set on role="tool" to tie a result back to the call that requested it.
    tool_call_id: str | None = None
    name: str | None = None


@dataclass(slots=True)
class ToolSpec:
    """A tool offered to the model, described in JSON Schema."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(slots=True)
class LLMResponse:
    # Either prose for the caller, or tool calls to run, or both.
    content: str | None
    tool_calls: list[ToolCall]
    provider: str
    model: str
    duration_ms: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    model: str

    async def complete(
        self, messages: list[Message], tools: list[ToolSpec] | None = None
    ) -> LLMResponse: ...


# ---------- Text to speech ----------


@dataclass(slots=True)
class Audio:
    data: bytes
    mime_type: str
    provider: str
    # How long synthesis took, matching Transcript.duration_ms and
    # LLMResponse.duration_ms. This is the number that becomes `tts_ms`.
    duration_ms: int
    # How long the audio itself plays for. Distinct from the above, and
    # conflating the two hides which one is actually the problem.
    audio_ms: int | None = None
    sample_rate: int | None = None


@runtime_checkable
class TTSProvider(Protocol):
    name: str

    async def synthesize(self, text: str) -> Audio: ...
