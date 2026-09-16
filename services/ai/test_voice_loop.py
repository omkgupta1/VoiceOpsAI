#!/usr/bin/env python
"""
End-to-end voice loop check.

Synthesises a spoken question with Piper, posts the audio to /v1/turn/audio,
and asserts the agent heard it well enough to call the right tool successfully.

The point is the round trip, not the wording of the reply: this catches the
failure mode that text tests structurally cannot — speech recognition mangling
an identifier so a lookup that should succeed does not. `AI858` transcribing as
`AI 858` or `AIA-858` is exactly the bug this exists to find.

Requires: the AI service on :8000, the flight service on :8002, piper on PATH.

    make test-voice
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

from app.config import resolve_path, settings

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"

AI_URL = f"http://localhost:{settings.ai_port}/v1/turn/audio"

# (spoken phrase, tool that must be called and succeed)
CASES: list[tuple[str, str]] = [
    ("Is flight AI858 delayed?", "check_flight_status"),
    ("What is the status of flight AI303?", "check_flight_status"),
    ("I want to look up booking 7MGFXC", "get_booking"),
    ("Can you put me through to a human agent?", "escalate_to_human"),
]


def speak(text: str, target: Path) -> None:
    subprocess.run(
        [settings.piper_bin, "-m", str(resolve_path(settings.piper_voice)), "-f", str(target)],
        input=text.encode(), capture_output=True, check=True,
    )


async def main() -> int:
    print(f"\n  voice loop  {DIM}piper -> /v1/turn/audio -> whisper -> llm -> tools{RESET}\n")

    passed = 0
    async with httpx.AsyncClient(timeout=180.0) as client:
        with tempfile.TemporaryDirectory(prefix="voiceops-loop-") as tmp:
            for phrase, expected in CASES:
                wav = Path(tmp) / "q.wav"
                speak(phrase, wav)

                response = await client.post(
                    AI_URL, files={"audio": ("q.wav", wav.read_bytes(), "audio/wav")}
                )
                if response.status_code != 200:
                    print(f"  {RED}fail{RESET}  {phrase}\n        HTTP {response.status_code}")
                    continue

                body = response.json()
                called = {c["name"] for c in body["tool_calls"] if c["ok"]}
                ok = expected in called
                passed += ok

                mark = f"{GREEN}pass{RESET}" if ok else f"{RED}fail{RESET}"
                timings = body["timings"]
                print(f"  {mark}  {phrase}")
                print(f"        {DIM}heard{RESET} {body['transcript']!r}")
                print(f"        {DIM}tools{RESET} {sorted(called) or '(none)'}  "
                      f"{DIM}stt{RESET} {timings['stt_ms']}ms "
                      f"{DIM}llm{RESET} {timings['llm_ms']}ms "
                      f"{DIM}tts{RESET} {timings['tts_ms']}ms")
                if not ok:
                    print(f"        {RED}expected {expected}{RESET}")
                print()

    total = len(CASES)
    colour = GREEN if passed == total else RED
    print(f"  {colour}{passed}/{total} voice turns resolved correctly{RESET}\n")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
