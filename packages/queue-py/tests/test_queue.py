"""
Queue behaviour, against a real Redis.

Not mocked. The properties checked here — atomic reservation, lease expiry,
priority ordering — are properties of Redis semantics, and a mock would only
assert that the mock behaves as I imagined Redis does.

    make test-queue          # everything
    pytest -m "not integration"   # the pure half, no services needed
"""

from __future__ import annotations

import asyncio
import time

import pytest
from voiceops_queue import Job, Priority, QueueConsumer, Status

pytestmark = pytest.mark.integration


async def reserve_eventually(consumer: QueueConsumer, give_up_after: float = 5.0) -> Job | None:
    """
    Reserve, waiting out any backoff — what a scheduler and a worker do together.

    Backoff carries jitter, so a retry's exact due time is not knowable in
    advance. Polling is both realistic and the only way to test a deliberately
    non-deterministic delay without asserting on the delay itself.
    """
    deadline = time.time() + give_up_after
    while time.time() < deadline:
        await consumer.promote_due()
        if (job := await consumer.reserve()) is not None:
            return job
        await asyncio.sleep(0.05)
    return None


# ---------------------------------------------------------------- priority

async def test_highest_priority_is_served_first(queue) -> None:
    client, consumer = await queue()

    # Enqueued worst-first on purpose: were ordering coming from insertion order
    # rather than priority, this would come back out in the order it went in.
    for priority in (Priority.LOW, Priority.MEDIUM, Priority.HIGH, Priority.LOW):
        await client.enqueue(Job(job_type="scheduled_callback", priority=priority))

    order = []
    while (job := await consumer.reserve()) is not None:
        order.append(str(job.priority))

    assert order == ["HIGH", "MEDIUM", "LOW", "LOW"]


# ---------------------------------------------------------------- crash recovery

async def test_a_reserved_job_leaves_the_ready_list(queue) -> None:
    client, consumer = await queue()
    await client.enqueue(Job(job_type="scheduled_callback"))

    assert await consumer.reserve() is not None

    stats = await client.stats()
    assert stats["ready_total"] == 0
    # Held, not lost: no ack and no nack is exactly what `kill -9` leaves behind.
    assert stats["processing"] == 1


async def test_a_live_lease_is_not_reaped(queue) -> None:
    client, consumer = await queue()
    await client.enqueue(Job(job_type="scheduled_callback"))
    await consumer.reserve()

    assert await consumer.reap_expired() == 0


async def test_an_expired_lease_returns_the_job(queue) -> None:
    """The whole point of the processing set: a worker that dies loses no work."""
    client, consumer = await queue()
    job, _ = await client.enqueue(Job(job_type="scheduled_callback"))
    await consumer.reserve()

    await asyncio.sleep(1.1)  # visibility_timeout is 1.0s in the fixture

    assert await consumer.reap_expired() == 1
    stats = await client.stats()
    assert stats["ready_total"] == 1
    assert stats["processing"] == 0

    again = await consumer.reserve()
    assert again is not None
    assert again.id == job.id
    # A recovered job has been tried once already, and the count must say so —
    # otherwise a job that reliably kills its worker retries for ever.
    assert again.attempt == 2


# ---------------------------------------------------------------- retry and DLQ

async def test_transient_failures_retry_then_dead_letter(queue) -> None:
    # base=1.0 keeps each backoff under a second so this stays quick; the curve
    # itself is asserted in test_backoff.py, where no I/O is involved.
    client, consumer = await queue(backoff_base=1.0)
    await client.enqueue(Job(job_type="scheduled_callback", max_attempts=3))

    outcomes, delays = [], []
    for _ in range(3):
        job = await reserve_eventually(consumer)
        assert job is not None, "job never became due"
        status, delay = await consumer.nack(
            job, error="upstream is down", code="SERVICE_UNAVAILABLE"
        )
        outcomes.append(str(status))
        delays.append(delay)

    assert outcomes == ["RETRY_WAIT", "RETRY_WAIT", "DEAD_LETTER"]
    # Each retry is delayed; giving up is not a retry, so it carries no delay.
    assert delays[0] is not None and delays[1] is not None
    assert delays[2] is None

    assert len(await client.dead_letters()) == 1


async def test_an_operator_can_requeue_a_dead_letter(queue) -> None:
    client, consumer = await queue(backoff_base=1.0)
    await client.enqueue(Job(job_type="scheduled_callback", max_attempts=1))
    job = await reserve_eventually(consumer)
    await consumer.nack(job, error="down", code="SERVICE_UNAVAILABLE")

    dead = await client.dead_letters()
    assert len(dead) == 1

    requeued = await client.requeue_dead_letter(dead[0].id)

    assert requeued is not None
    # Attempts reset — which is why the dashboard tells an operator to fix the
    # cause first, or the job simply exhausts again.
    assert requeued.attempt == 0
    assert (await client.stats())["dead_letter"] == 0


async def test_a_permanent_failure_is_never_retried(queue) -> None:
    client, consumer = await queue()
    await client.enqueue(Job(job_type="cancel_booking", max_attempts=5))
    job = await consumer.reserve()

    status, delay = await consumer.nack(
        job, error="no booking matches that PNR", code="BOOKING_NOT_FOUND", status_code=404
    )

    assert status is Status.FAILED
    assert delay is None
    assert job.attempt == 1, "four attempts left, and none of them would have helped"

    stats = await client.stats()
    assert stats["ready_total"] == 0
    assert stats["scheduled"] == 0
    assert stats["dead_letter"] == 0


# ---------------------------------------------------------------- idempotency

async def test_the_same_key_returns_the_original_job(queue) -> None:
    """
    The guard against cancelling a booking twice when a caller retries an
    enqueue it never saw succeed (overview §1).
    """
    client, _ = await queue()
    spec = {"job_type": "cancel_booking", "payload": {"pnr": "ABC123"},
            "idempotency_key": "cancel:ABC123"}

    first, created_first = await client.enqueue(Job(**spec))
    second, created_second = await client.enqueue(Job(**spec))

    assert first.id == second.id
    assert created_first is True
    assert created_second is False
    assert (await client.stats())["ready_total"] == 1


# ---------------------------------------------------------------- scheduling

async def test_a_future_job_waits_until_it_is_due(queue) -> None:
    client, consumer = await queue()
    await client.enqueue(Job(job_type="scheduled_callback", run_at=time.time() + 0.5))

    assert (await client.stats())["scheduled"] == 1
    assert await consumer.reserve() is None
    assert await consumer.promote_due() == 0

    await asyncio.sleep(0.6)

    assert await consumer.promote_due() == 1
    assert await consumer.reserve() is not None


# ---------------------------------------------------------------- propagation

async def test_trace_context_survives_the_queue(queue) -> None:
    """
    A job carries the trace of the call that queued it, so the attempt a worker
    runs half a minute later lands in that call's trace (ADR 0006).
    """
    client, consumer = await queue()
    headers = {"traceparent": "00-" + "c" * 32 + "-" + "d" * 16 + "-01"}
    await client.enqueue(Job(job_type="cancel_booking", trace_context=headers))

    reserved = await consumer.reserve()

    assert reserved is not None
    assert reserved.trace_context == headers
