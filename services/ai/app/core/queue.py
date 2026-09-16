"""
Enqueueing follow-up work from a live call.

When an operation the customer already confirmed fails for a transient reason,
the work should not die with the call. The customer said "yes, cancel it"; the
flight service being briefly unreachable is not their problem, and asking them to
call back to repeat an instruction they already gave is the failure this queue
exists to prevent.
"""

from __future__ import annotations

import logging

from app.config import settings
from voiceops_queue import Job, Priority, QueueClient

log = logging.getLogger("ai.queue")

_client: QueueClient | None = None


async def open_client() -> None:
    global _client
    _client = QueueClient(settings.redis_url, namespace=settings.queue_namespace)


async def close_client() -> None:
    if _client is not None:
        await _client.close()


async def enqueue_retry(
    *, tool: str, arguments: dict, call_id: str | None, error: str
) -> Job | None:
    """
    Queue a confirmed-but-failed operation for completion in the background.

    The idempotency key is derived from the call and the exact operation, so a
    customer who repeats themselves — or a turn that is retried — produces one
    job, not two cancellations of the same booking.
    """
    if _client is None:
        return None

    identifier = arguments.get("pnr") or arguments.get("flight_number") or "unknown"
    try:
        job, created = await _client.enqueue(
            Job(
                job_type=tool,
                payload=dict(arguments),
                # A customer is waiting on the other end of this one.
                priority=Priority.HIGH,
                call_id=call_id,
                max_attempts=settings.max_attempts,
                idempotency_key=f"{call_id}:{tool}:{identifier}",
            )
        )
    except Exception as exc:  # noqa: BLE001 - a queue outage must not break the call
        log.warning("could not queue %s for retry: %s", tool, exc)
        return None

    log.info(
        "queued %s for retry (job=%s new=%s) after: %s", tool, job.id[:8], created, error
    )
    return job


async def stats() -> dict:
    return await _client.stats() if _client else {}
