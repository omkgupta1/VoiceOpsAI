#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["voiceops-queue"]
#
# [tool.uv.sources]
# voiceops-queue = { path = "packages/queue-py", editable = true }
# ///
"""Print queue depth, counters and any dead-lettered jobs."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from voiceops_queue import QueueClient  # noqa: E402

GREEN, YELLOW, RED, DIM, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"


def _from_env(name: str, default: str) -> str:
    """
    Read a setting the same way the services do.

    This script originally used the library default namespace while the services
    read QUEUE_NAMESPACE from .env, so it cheerfully reported an empty queue
    while jobs were retrying in a different keyspace. Monitoring that looks at
    somewhere other than production is worse than no monitoring.
    """
    if value := os.environ.get(name):
        return value
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.strip().startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip("\"'")
    return default


def redis_url() -> str:
    return _from_env("REDIS_URL", "redis://localhost:6379/0")


def namespace() -> str:
    return _from_env("QUEUE_NAMESPACE", "vo")


async def main() -> int:
    watch = "--watch" in sys.argv
    client = QueueClient(redis_url(), namespace=namespace())

    try:
        while True:
            stats = await client.stats()
            ready = stats["ready"]
            counters = stats["counters"]

            if watch:
                print("\033[2J\033[H", end="")
            print(f"\n  queue depth   {DIM}(namespace {namespace()}){RESET}")
            for priority, colour in (("HIGH", RED), ("MEDIUM", YELLOW), ("LOW", DIM)):
                print(f"    {colour}{priority:<7}{RESET} {ready.get(priority, 0)}")
            print(f"    {DIM}scheduled / retry-wait{RESET}  {stats['scheduled']}")
            print(f"    {DIM}in flight{RESET}               {stats['processing']}")
            dlq = stats["dead_letter"]
            print(f"    {(RED if dlq else DIM)}dead letter{RESET}             {dlq}")

            print(f"\n  lifetime counters")
            for name in ("enqueued", "succeeded", "retried", "failed", "dead_lettered", "reaped"):
                print(f"    {name:<15} {counters.get(name, 0)}")

            if dlq:
                print(f"\n  {RED}dead-lettered jobs{RESET}")
                for job in await client.dead_letters(10):
                    print(f"    {job.id[:8]}  {job.job_type:<20} {job.attempt} attempts  "
                          f"{DIM}{(job.last_error or '')[:50]}{RESET}")
            print()

            if not watch:
                return 0
            time.sleep(1)
    finally:
        await client.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
