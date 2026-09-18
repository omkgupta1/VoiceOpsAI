"""
Enqueueing and inspecting the queue.

The half of the protocol that producers need. The AI service uses this; so will
the Node API in Phase 7. Consuming lives in `consumer.py`.
"""

from __future__ import annotations

import time
from typing import Any

import redis.asyncio as redis

from voiceops_queue.keys import DEFAULT_NAMESPACE, Keys
from voiceops_queue.models import PRIORITY_ORDER, Job, Priority, Status
from voiceops_queue.propagation import current_carrier


class QueueClient:
    def __init__(self, url: str, namespace: str = DEFAULT_NAMESPACE) -> None:
        self.redis: redis.Redis = redis.from_url(url, decode_responses=True)
        self.keys = Keys(namespace)

    async def close(self) -> None:
        await self.redis.aclose()

    async def enqueue(self, job: Job) -> tuple[Job, bool]:
        """
        Add a job. Returns the job and whether it was newly created.

        When `idempotency_key` is set, a second enqueue with the same key returns
        the original job rather than creating a second one. This is what stops a
        caller that retried after a timeout from cancelling the same booking
        twice — the duplicate-execution problem from overview.md §1.
        """
        if job.idempotency_key:
            key = self.keys.idempotency(job.idempotency_key)
            # SET NX is the whole mechanism: whoever sets it first owns the job.
            claimed = await self.redis.set(key, job.id, nx=True, ex=86_400)
            if not claimed:
                existing_id = await self.redis.get(key)
                if existing := await self.get(existing_id):
                    return existing, False

        # Captured here, at the moment of enqueue, because this is the only point
        # where the calling trace is still on the stack. By the time a worker
        # reserves the job the caller is long gone.
        if not job.trace_context:
            job.trace_context = current_carrier()

        due = job.run_at <= time.time()
        job.status = Status.QUEUED if due else Status.SCHEDULED

        pipe = self.redis.pipeline()
        pipe.hset(self.keys.job(job.id), mapping=job.to_redis())
        if due:
            pipe.rpush(self.keys.ready(job.priority), job.id)
        else:
            pipe.zadd(self.keys.scheduled, {job.id: job.run_at})
        pipe.incr(self.keys.stat("enqueued"))
        await pipe.execute()

        return job, True

    async def get(self, job_id: str | None) -> Job | None:
        if not job_id:
            return None
        raw = await self.redis.hgetall(self.keys.job(job_id))
        return Job.from_redis(raw) if raw else None

    async def stats(self) -> dict[str, Any]:
        """Queue depth and counters — what the dashboard and CloudWatch both need."""
        pipe = self.redis.pipeline()
        for priority in PRIORITY_ORDER:
            pipe.llen(self.keys.ready(priority))
        pipe.zcard(self.keys.scheduled)
        pipe.zcard(self.keys.processing)
        pipe.llen(self.keys.dlq)
        for counter in ("enqueued", "succeeded", "failed", "retried", "dead_lettered", "reaped"):
            pipe.get(self.keys.stat(counter))
        results = await pipe.execute()

        ready = dict(zip((str(p) for p in PRIORITY_ORDER), results[:3]))
        counters = results[6:]
        return {
            "ready": ready,
            "ready_total": sum(ready.values()),
            "scheduled": results[3],
            "processing": results[4],
            "dead_letter": results[5],
            "counters": {
                name: int(value or 0)
                for name, value in zip(
                    ("enqueued", "succeeded", "failed", "retried", "dead_lettered", "reaped"),
                    counters,
                )
            },
        }

    async def dead_letters(self, limit: int = 50) -> list[Job]:
        ids = await self.redis.lrange(self.keys.dlq, 0, limit - 1)
        jobs = [await self.get(job_id) for job_id in ids]
        return [job for job in jobs if job]

    async def requeue_dead_letter(self, job_id: str) -> Job | None:
        """
        Put a dead-lettered job back, attempts reset.

        Manual and deliberate: a job reaches the DLQ because automation gave up,
        so something should have changed before it is tried again. The Phase 8
        dashboard exposes this as an operator action.
        """
        job = await self.get(job_id)
        if job is None:
            return None

        job.attempt = 0
        job.status = Status.QUEUED
        job.run_at = time.time()
        job.last_error = None
        job.last_error_code = None

        pipe = self.redis.pipeline()
        pipe.lrem(self.keys.dlq, 1, job_id)
        pipe.hset(self.keys.job(job_id), mapping=job.to_redis())
        pipe.rpush(self.keys.ready(job.priority), job_id)
        await pipe.execute()
        return job

    async def purge(self) -> None:
        """Delete every key in this namespace. Tests and local resets only."""
        cursor = 0
        while True:
            cursor, batch = await self.redis.scan(cursor, match=f"{self.keys.ns}:*", count=500)
            if batch:
                await self.redis.delete(*batch)
            if cursor == 0:
                break
