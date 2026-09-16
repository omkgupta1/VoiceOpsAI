"""
Piper — the local text-to-speech provider.

A small ONNX model that synthesises faster than real time on CPU, so it costs
nothing and adds little latency. Reads text on stdin and writes a WAV file.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import time
import wave
from pathlib import Path

from app.config import resolve_path, settings
from app.providers.base import Audio, ProviderError


class PiperTTS:
    name = "piper"

    def __init__(self, binary: str | None = None, voice: str | None = None) -> None:
        self.binary = binary or settings.piper_bin
        self.voice_path = resolve_path(voice or settings.piper_voice)

    async def synthesize(self, text: str) -> Audio:
        text = (text or "").strip()
        if not text:
            raise ProviderError(self.name, "No text supplied", retryable=False, code="EMPTY_TEXT")
        if shutil.which(self.binary) is None:
            raise ProviderError(
                self.name,
                f"'{self.binary}' not found — run `uv tool install piper-tts`",
                retryable=False, code="BINARY_MISSING",
            )
        if not self.voice_path.is_file():
            raise ProviderError(
                self.name,
                f"Piper voice missing at {self.voice_path} — run `make models`",
                retryable=False, code="VOICE_MISSING",
            )

        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="voiceops-tts-") as tmp:
            output = Path(tmp) / "speech.wav"

            process = await asyncio.create_subprocess_exec(
                self.binary, "-m", str(self.voice_path), "-f", str(output),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, err = await process.communicate(input=text.encode())

            if process.returncode != 0 or not output.is_file():
                raise ProviderError(
                    self.name,
                    f"piper exited {process.returncode}: {err.decode(errors='replace')[:200]}",
                    retryable=False, code="SYNTHESIS_FAILED",
                )

            data = output.read_bytes()
            with wave.open(str(output), "rb") as handle:
                sample_rate = handle.getframerate()
                # Audio length, as distinct from how long synthesis took —
                # both matter, and conflating them hides which is the problem.
                audio_ms = int(handle.getnframes() / float(sample_rate) * 1000)

        return Audio(
            data=data,
            mime_type="audio/wav",
            provider=self.name,
            duration_ms=int((time.perf_counter() - started) * 1000),
            audio_ms=audio_ms,
            sample_rate=sample_rate,
        )
