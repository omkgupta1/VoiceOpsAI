"""
whisper.cpp — the local speech-to-text provider.

Runs as a subprocess against a GGML model, Metal-accelerated on Apple Silicon.
Free, offline, and fast enough for conversational use on an M-series machine.

whisper.cpp only accepts 16 kHz mono WAV, while browsers record WebM/Opus, so
every input is normalised through ffmpeg first. Skipping that conversion is the
single most common reason local STT "silently returns nothing".
"""

from __future__ import annotations

import asyncio
import re
import shutil
import tempfile
import time
from pathlib import Path

from app.config import resolve_path, settings
from app.providers.base import ProviderError, Transcript

# whisper.cpp emits "[00:00:00.000 --> 00:00:02.000]  text" unless -nt is passed;
# strip any that survive so callers never see timing noise in the transcript.
_TIMESTAMP = re.compile(r"\[\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3}\]\s*")

# Lines whisper.cpp emits for silence. Treated as "nothing was said" rather than
# transcribed literally, which would otherwise reach the LLM as a user utterance.
_NON_SPEECH = {"[BLANK_AUDIO]", "[SILENCE]", "(silence)", "[MUSIC]", "[NOISE]"}


async def _run(command: list[str], *, stdin: bytes | None = None) -> tuple[int, bytes, bytes]:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE if stdin else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await process.communicate(input=stdin)
    return process.returncode or 0, out, err


class LocalWhisperSTT:
    name = "local"

    def __init__(self, binary: str | None = None, model: str | None = None) -> None:
        self.binary = binary or settings.whisper_bin
        self.model_path = resolve_path(model or settings.whisper_model)

    async def transcribe(self, audio: bytes, *, mime_type: str = "audio/wav") -> Transcript:
        if not audio:
            raise ProviderError(self.name, "No audio supplied", retryable=False, code="EMPTY_AUDIO")
        if shutil.which(self.binary) is None:
            raise ProviderError(
                self.name,
                f"'{self.binary}' not found — run `brew install whisper-cpp`",
                retryable=False, code="BINARY_MISSING",
            )
        if not self.model_path.is_file():
            raise ProviderError(
                self.name,
                f"Whisper model missing at {self.model_path} — run `make models`",
                retryable=False, code="MODEL_MISSING",
            )

        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="voiceops-stt-") as tmp:
            source = Path(tmp) / "input"
            source.write_bytes(audio)
            wav = Path(tmp) / "audio.wav"

            await self._to_wav(source, wav)

            code, out, err = await _run(
                [
                    self.binary,
                    "-m", str(self.model_path),
                    "-f", str(wav),
                    "-nt",          # no timestamps
                    "-np",          # no progress chatter on stderr
                    "-l", "en",
                ]
            )
            if code != 0:
                # A non-zero exit here is usually a corrupt or truncated upload,
                # which retrying cannot fix.
                raise ProviderError(
                    self.name,
                    f"whisper-cli exited {code}: {err.decode(errors='replace')[:200]}",
                    retryable=False, code="TRANSCRIPTION_FAILED",
                )

        text = _TIMESTAMP.sub("", out.decode(errors="replace")).strip()
        text = " ".join(
            line.strip() for line in text.splitlines()
            if line.strip() and line.strip() not in _NON_SPEECH
        ).strip()

        return Transcript(
            text=text,
            provider=self.name,
            duration_ms=int((time.perf_counter() - started) * 1000),
            language="en",
        )

    async def _to_wav(self, source: Path, target: Path) -> None:
        """Normalise any input to the 16 kHz mono WAV whisper.cpp requires."""
        if shutil.which("ffmpeg") is None:
            raise ProviderError(
                self.name,
                "ffmpeg not found — run `brew install ffmpeg`. whisper.cpp only "
                "accepts 16kHz mono WAV, and browsers record WebM/Opus.",
                retryable=False, code="FFMPEG_MISSING",
            )

        code, _, err = await _run(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-y",
             "-i", str(source), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(target)]
        )
        if code != 0:
            raise ProviderError(
                self.name,
                f"ffmpeg could not decode the audio: {err.decode(errors='replace')[:200]}",
                retryable=False, code="AUDIO_DECODE_FAILED",
            )
