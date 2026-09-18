#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx"]
# ///
"""
Prove that one voice call is a single trace across every service.

This is the Phase 9 verification from the plan, run as a script rather than by
clicking around Jaeger — a check nobody can run in one command is a check that
stops being run.

    make test-trace

It signs in, sends a real turn through the platform API, then asks Jaeger for the
trace by the id the API handed back and reports which services appear in it.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def _env(name: str, default: str) -> str:
    if value := os.environ.get(name):
        return value
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.strip().startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip("\"'")
    return default


API = _env("API_URL", "http://127.0.0.1:3000")
JAEGER = _env("JAEGER_URL", "http://localhost:16686")
QUESTION = "What is the status of flight 6E597?"


def ok(message: str) -> None:
    print(f"  {GREEN}pass{RESET}  {message}")


def bad(message: str) -> None:
    print(f"  {RED}FAIL{RESET}  {message}")


async def main() -> int:
    print(f"\n{DIM}Sending one turn through the platform API…{RESET}\n")

    async with httpx.AsyncClient(timeout=180.0) as http:
        try:
            auth = await http.post(
                f"{API}/api/auth/login",
                json={"email": "supervisor@voiceops.ai", "password": "voiceops123"},
            )
            auth.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            bad(f"could not sign in to {API} — is `make api` running? ({exc})")
            return 1

        token = auth.json()["token"]
        started = time.perf_counter()
        turn = await http.post(
            f"{API}/api/voice/turn",
            headers={"authorization": f"Bearer {token}"},
            json={"message": QUESTION},
        )
        elapsed = time.perf_counter() - started

        if turn.status_code != 200:
            bad(f"turn failed: {turn.status_code} {turn.text[:200]}")
            return 1

        trace_id = turn.headers.get("x-trace-id")
        if not trace_id:
            bad("the API did not return an x-trace-id header")
            return 1

        ok(f"turn answered in {elapsed:.1f}s")
        print(f"  {DIM}reply: {turn.json().get('reply', '')[:90]}{RESET}")
        print(f"  {DIM}trace: {trace_id}{RESET}\n")

        # Spans are batched before export, so the trace is not queryable the
        # instant the request returns. Poll rather than sleeping a guessed
        # interval, which is flaky in exactly one direction.
        print(f"{DIM}Waiting for spans to reach Jaeger…{RESET}\n")
        spans: list[dict] = []
        for _ in range(30):
            await asyncio.sleep(1.0)
            try:
                found = await http.get(f"{JAEGER}/api/traces/{trace_id}")
                data = found.json().get("data") or []
                if data:
                    spans = data[0].get("spans", [])
                    processes = data[0].get("processes", {})
                    if len(spans) > 3:
                        break
            except Exception:  # noqa: BLE001 - jaeger may still be starting
                continue

        if not spans:
            bad(f"no trace {trace_id[:16]}… in Jaeger — is `make up` running?")
            return 1

        services = {
            processes.get(span.get("processID"), {}).get("serviceName", "?")
            for span in spans
        }
        names = sorted(span["operationName"] for span in spans)

        ok(f"{len(spans)} spans in one trace")

        failures = 0
        for service in ("api", "ai", "flight-mock"):
            if service in services:
                ok(f"{service} appears in the trace")
            else:
                bad(f"{service} is missing from the trace")
                failures += 1

        # The spans that carry this project's meaning, as opposed to the HTTP
        # and SQL ones auto-instrumentation produces for free.
        for fragment, label in (
            ("llm.complete", "the LLM call is its own span"),
            ("tool ", "the tool call is its own span"),
        ):
            if any(fragment in name for name in names):
                ok(label)
            else:
                bad(f"missing: {label}")
                failures += 1

        print(f"\n{DIM}  spans:{RESET}")
        for name in names:
            print(f"{DIM}    {name}{RESET}")

        print(f"\n  {YELLOW}{JAEGER}/trace/{trace_id}{RESET}\n")
        return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
