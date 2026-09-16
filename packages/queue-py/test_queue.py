#!/usr/bin/env python
"""
Queue behaviour tests, against a real Redis.

Not mocked. The properties being checked here — atomic reservation, lease
expiry, priority ordering — are properties of Redis semantics, and a mock would
only assert that the mock behaves as I imagined Redis does.

    make test-queue
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

from voiceops_queue import Job, Priority, QueueClient, QueueConsumer, Status, backoff_seconds
from voiceops_queue.errors import classify

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
NS = "votest"

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    mark = f"{GREEN}pass{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  {mark}  {name}{'' if ok else '  — ' + detail}")


async def fresh(backoff_base: float = 2.0) -> tuple[QueueClient, QueueConsumer]:
    client = QueueClient(REDIS_URL, namespace=NS)
    await client.purge()
    consumer = QueueConsumer(
        REDIS_URL, namespace=NS, worker_id="test",
        visibility_timeout=1.0, backoff_base=backoff_base,
    )
    return client, consumer


async def reserve_eventually(consumer: QueueConsumer, timeout: float = 5.0) -> Job | None:
    """
    Reserve, waiting out any backoff — what the scheduler and a worker do together.

    Backoff carries jitter, so a retry's exact due time is not knowable in
    advance. Polling for it is both realistic and the only way to test a
    deliberately non-deterministic delay without asserting on the delay itself.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        await consumer.promote_due()
        if (job := await consumer.reserve()) is not None:
            return job
        await asyncio.sleep(0.05)
    return None


async def test_priority_ordering() -> None:
    client, consumer = await fresh()
    # Enqueued worst-first on purpose: if ordering came from insertion order
    # rather than priority, this would come back out in the same order.
    for priority in (Priority.LOW, Priority.MEDIUM, Priority.HIGH, Priority.LOW):
        await client.enqueue(Job(job_type="scheduled_callback", priority=priority))

    order = []
    while (job := await consumer.reserve()) is not None:
        order.append(str(job.priority))

    check("highest priority is served first", order == ["HIGH", "MEDIUM", "LOW", "LOW"], str(order))
    await client.close(); await consumer.close()


async def test_crash_recovery() -> None:
    client, consumer = await fresh()
    job, _ = await client.enqueue(Job(job_type="scheduled_callback"))

    reserved = await consumer.reserve()
    check("a reserved job leaves the ready list", reserved is not None and
          (await client.stats())["ready_total"] == 0)

    # The worker now dies. No ack, no nack — exactly what kill -9 leaves behind.
    check("an in-flight job is held, not lost", (await client.stats())["processing"] == 1)

    # Nothing to recover while the lease is still valid.
    check("a live lease is not reaped", await consumer.reap_expired() == 0)

    await asyncio.sleep(1.1)  # lease expires
    recovered = await consumer.reap_expired()
    stats = await client.stats()
    check("an expired lease returns the job to the queue",
          recovered == 1 and stats["ready_total"] == 1 and stats["processing"] == 0,
          f"recovered={recovered} stats={stats['ready']}")

    again = await consumer.reserve()
    check("the recovered job can be reserved again and counts as a new attempt",
          again is not None and again.id == job.id and again.attempt == 2,
          f"attempt={again.attempt if again else None}")
    await client.close(); await consumer.close()


async def test_retry_then_dead_letter() -> None:
    # base=1.0 keeps each backoff under a second so the test stays quick; the
    # curve itself is asserted separately in test_backoff_curve.
    client, consumer = await fresh(backoff_base=1.0)
    await client.enqueue(Job(job_type="scheduled_callback", max_attempts=3))

    outcomes, delays = [], []
    for _ in range(3):
        job = await reserve_eventually(consumer)
        if job is None:
            check("a transient failure retries until attempts run out", False, "job never became due")
            return
        status, delay = await consumer.nack(
            job, error="upstream is down", code="SERVICE_UNAVAILABLE"
        )
        outcomes.append(str(status))
        delays.append(delay)

    check("a transient failure retries until attempts run out, then dead-letters",
          outcomes == ["RETRY_WAIT", "RETRY_WAIT", "DEAD_LETTER"], str(outcomes))
    check("each retry is delayed, and the final give-up is not",
          delays[0] is not None and delays[1] is not None and delays[2] is None,
          str(delays))
    check("the dead-lettered job is retrievable for a human",
          len(await client.dead_letters()) == 1)

    requeued = await client.requeue_dead_letter((await client.dead_letters())[0].id)
    check("an operator can requeue it with attempts reset",
          requeued is not None and requeued.attempt == 0 and
          (await client.stats())["dead_letter"] == 0)
    await client.close(); await consumer.close()


async def test_permanent_is_never_retried() -> None:
    client, consumer = await fresh()
    await client.enqueue(Job(job_type="cancel_booking", max_attempts=5))
    job = await consumer.reserve()

    status, delay = await consumer.nack(
        job, error="no booking matches that PNR", code="BOOKING_NOT_FOUND", status_code=404
    )
    stats = await client.stats()
    check("a permanent failure fails immediately, with attempts still unused",
          status is Status.FAILED and delay is None and job.attempt == 1,
          f"status={status} attempt={job.attempt}")
    check("a permanent failure is not queued for retry",
          stats["ready_total"] == 0 and stats["scheduled"] == 0 and stats["dead_letter"] == 0)
    await client.close(); await consumer.close()


async def test_idempotency() -> None:
    client, consumer = await fresh()
    first, created_first = await client.enqueue(
        Job(job_type="cancel_booking", payload={"pnr": "ABC123"}, idempotency_key="cancel:ABC123")
    )
    second, created_second = await client.enqueue(
        Job(job_type="cancel_booking", payload={"pnr": "ABC123"}, idempotency_key="cancel:ABC123")
    )

    check("the same idempotency key returns the original job",
          first.id == second.id and created_first and not created_second)
    check("and does not enqueue the work twice", (await client.stats())["ready_total"] == 1)
    await client.close(); await consumer.close()


async def test_scheduling() -> None:
    client, consumer = await fresh()
    await client.enqueue(Job(job_type="scheduled_callback", run_at=time.time() + 0.5))

    check("a future job waits in the scheduled set", (await client.stats())["scheduled"] == 1)
    check("and is not reservable before it is due", await consumer.reserve() is None)
    check("promoting early moves nothing", await consumer.promote_due() == 0)

    await asyncio.sleep(0.6)
    check("promoting after it is due releases it", await consumer.promote_due() == 1)
    check("and it can then be reserved", await consumer.reserve() is not None)
    await client.close(); await consumer.close()


def test_backoff_curve() -> None:
    plain = [backoff_seconds(n, jitter=False) for n in range(1, 6)]
    check("backoff without jitter follows 2, 4, 8, 16, 32", plain == [2, 4, 8, 16, 32], str(plain))

    check("backoff is capped", backoff_seconds(20, jitter=False, cap=300.0) == 300.0)

    # Jitter stops every job that failed in one outage retrying in the same
    # instant and knocking the recovering service over again.
    samples = [backoff_seconds(4) for _ in range(200)]
    check("jitter spreads retries across the window",
          len(set(samples)) > 150 and all(0 < s <= 16 for s in samples),
          f"distinct={len(set(samples))} max={max(samples):.2f}")

    # Equal jitter, not full jitter. Full jitter draws from [0, delay] and so
    # routinely produces near-zero waits, which gives up the exponential growth
    # it is layered on: a real cancellation burned all five attempts in ~5s.
    check("every wait is at least half the intended backoff",
          all(s >= 8 for s in samples), f"min={min(samples):.2f}, want >= 8")


def test_classification() -> None:
    cases = [
        (dict(status_code=503), True,  "503 retries"),
        (dict(status_code=429), True,  "429 retries"),
        (dict(status_code=404), False, "404 does not retry"),
        (dict(status_code=422), False, "422 does not retry"),
        (dict(code="BOOKING_NOT_FOUND"), False, "a known permanent code does not retry"),
        (dict(code="UPSTREAM_TIMEOUT"), True,  "a known transient code retries"),
        # The upstream says retry; the status says we asked wrongly. The status wins.
        (dict(status_code=404, retryable_hint=True), False,
         "a 404 claiming to be retryable is still not retried"),
        (dict(), True, "an unclassified failure is retried, bounded by the attempt cap"),
    ]
    for kwargs, expected, name in cases:
        check(name, classify(**kwargs).retryable == expected)


async def main() -> int:
    print("\n  Queue behaviour\n")
    print(f"  {DIM}priority{RESET}")
    await test_priority_ordering()
    print(f"\n  {DIM}crash recovery{RESET}")
    await test_crash_recovery()
    print(f"\n  {DIM}retry, backoff and dead-lettering{RESET}")
    await test_retry_then_dead_letter()
    await test_permanent_is_never_retried()
    print(f"\n  {DIM}scheduling{RESET}")
    await test_scheduling()
    print(f"\n  {DIM}idempotency{RESET}")
    await test_idempotency()
    print(f"\n  {DIM}backoff curve{RESET}")
    test_backoff_curve()
    print(f"\n  {DIM}error classification{RESET}")
    test_classification()

    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n  {len(RESULTS) - len(failed)}/{len(RESULTS)} passed\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
