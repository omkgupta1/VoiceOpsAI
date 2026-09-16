#!/usr/bin/env python
"""
Enqueue a job by hand — for testing, and for operators.

    uv run python ../../scripts/enqueue.py cancel_booking --pnr ABC123 --priority HIGH
    uv run python ../../scripts/enqueue.py scheduled_callback --in 30
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

from voiceops_queue import Job, Priority, QueueClient

ROOT = Path(__file__).resolve().parent.parent


def _from_env(name: str, default: str) -> str:
    if value := os.environ.get(name):
        return value
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.strip().startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip("\"'")
    return default


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_type")
    parser.add_argument("--pnr")
    parser.add_argument("--flight-number")
    parser.add_argument("--priority", choices=["HIGH", "MEDIUM", "LOW"], default="MEDIUM")
    parser.add_argument("--in", dest="delay", type=float, default=0.0,
                        help="seconds from now the job becomes due")
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--idempotency-key")
    args = parser.parse_args()

    payload = {k: v for k, v in (("pnr", args.pnr), ("flight_number", args.flight_number)) if v}
    client = QueueClient(_from_env("REDIS_URL", "redis://localhost:6379/0"),
                         namespace=_from_env("QUEUE_NAMESPACE", "vo"))
    try:
        for _ in range(args.count):
            job, created = await client.enqueue(
                Job(
                    job_type=args.job_type,
                    payload=payload,
                    priority=Priority(args.priority),
                    run_at=time.time() + args.delay,
                    idempotency_key=args.idempotency_key,
                )
            )
            print(f"  {'queued' if created else 'existing'}  {job.id}  "
                  f"{job.job_type}  {job.priority}"
                  f"{f'  due in {args.delay:.0f}s' if args.delay else ''}")
    finally:
        await client.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
