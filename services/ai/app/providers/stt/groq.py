"""
Groq Whisper — hosted speech-to-text on a free tier.

Written against the documented API but NOT yet verified: no key is configured.
Set GROQ_API_KEY in .env and STT_PROVIDER=groq to exercise it.

Unlike the local provider this needs no ffmpeg step — Groq accepts the common
container formats directly.
"""

from __future__ import annotations

import time

import httpx

from app.config import settings
from app.providers.base import ProviderError, Transcript

API_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

_EXTENSIONS = {
    "audio/wav": "wav", "audio/x-wav": "wav", "audio/webm": "webm",
    "audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/flac": "flac",
}


class GroqSTT:
    name = "groq"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key or settings.groq_api_key
        self.model = model or settings.groq_stt_model

    async def transcribe(self, audio: bytes, *, mime_type: str = "audio/wav") -> Transcript:
        if not audio:
            raise ProviderError(self.name, "No audio supplied", retryable=False, code="EMPTY_AUDIO")
        if not self.api_key:
            raise ProviderError(
                self.name,
                "GROQ_API_KEY is not set — get a free key at https://console.groq.com/keys",
                retryable=False, code="MISSING_API_KEY",
            )

        filename = f"audio.{_EXTENSIONS.get(mime_type, 'wav')}"
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    API_URL,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    files={"file": (filename, audio, mime_type)},
                    data={"model": self.model, "response_format": "json", "language": "en"},
                )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                self.name, "Timed out", retryable=True, code="STT_TIMEOUT", cause=exc
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                self.name, f"Network error: {exc}", retryable=True,
                code="CONNECTION_FAILED", cause=exc,
            ) from exc

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
                self.name, f"Groq rejected the audio: {response.text[:200]}",
                retryable=False, code="TRANSCRIPTION_FAILED",
            )

        return Transcript(
            text=(response.json().get("text") or "").strip(),
            provider=self.name,
            duration_ms=int((time.perf_counter() - started) * 1000),
            language="en",
        )
