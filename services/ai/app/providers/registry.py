"""
Provider selection.

The only place in the codebase that knows which concrete provider is in use.
Everything downstream — orchestrator, tools, API — sees the protocols from
`base.py` and nothing else, which is what makes swapping a provider an
environment-variable change rather than a code change.

Instances are cached because construction reads configuration and resolves model
paths; there is no per-request state to keep.
"""

from __future__ import annotations

from functools import lru_cache

from app.config import settings
from app.providers.base import LLMProvider, ProviderError, STTProvider, TTSProvider
from app.providers.llm.gemini import GeminiLLM
from app.providers.llm.groq import GroqLLM
from app.providers.llm.ollama import OllamaLLM
from app.providers.stt.groq import GroqSTT
from app.providers.stt.local_whisper import LocalWhisperSTT
from app.providers.tts.piper import PiperTTS

STT_PROVIDERS = {"local": LocalWhisperSTT, "groq": GroqSTT}
LLM_PROVIDERS = {"ollama": OllamaLLM, "groq": GroqLLM, "gemini": GeminiLLM}
TTS_PROVIDERS = {"piper": PiperTTS}


def _build(kind: str, registry: dict, name: str):
    key = (name or "").strip().lower()
    if key not in registry:
        raise ProviderError(
            "registry",
            f"Unknown {kind} provider '{name}'. Available: {', '.join(sorted(registry))}",
            retryable=False,
            code="UNKNOWN_PROVIDER",
        )
    return registry[key]()


@lru_cache(maxsize=1)
def get_stt() -> STTProvider:
    return _build("STT", STT_PROVIDERS, settings.stt_provider)


@lru_cache(maxsize=1)
def get_llm() -> LLMProvider:
    return _build("LLM", LLM_PROVIDERS, settings.llm_provider)


@lru_cache(maxsize=1)
def get_tts() -> TTSProvider:
    return _build("TTS", TTS_PROVIDERS, settings.tts_provider)


def active() -> dict[str, str]:
    """What is actually configured — surfaced on /health and stamped on every turn."""
    return {
        "stt": settings.stt_provider,
        "llm": f"{settings.llm_provider}:{get_llm().model}",
        "tts": settings.tts_provider,
    }
