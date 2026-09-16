"""
The worker and scheduler.

    uv run python -m app.main                  # workers + scheduler (local default)
    uv run python -m app.main --role worker
    uv run python -m app.main --role scheduler

Two loops:

**Workers** reserve a job, run its handler, and acknowledge or fail it. Several
run concurrently in one process; each holds one job at a time, so concurrency is
exactly the number of jobs in flight.

**The scheduler** promotes jobs whose time has come, and reaps leases whose
worker died. It is a separate role because it must keep running when every worker
is busy — a reaper that cannot run while workers are saturated cannot recover a
worker that died while saturated, which is precisely when they die.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import time
import uuid

from app import mirror
from app.config import settings
from app.handlers import HANDLERS, JobFailure
from voiceops_queue import Job, QueueClient, QueueConsumer, Status, classify

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)-18s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("worker")

_shutdown = asyncio.Event()


async def process(consumer: QueueConsumer, job: Job) -> None:
    """Run one job and record what happened."""
    started = time.perf_counter()
    handler = HANDLERS.get(job.job_type)

    await mirror.upsert_job(job, str(Status.PROCESSING))

    if handler is None:
        # An unknown job type is a deployment mistake, not a transient fault.
        # Retrying it five times would only delay noticing.
        await consumer.nack(
            job, error=f"no handler for job type '{job.job_type}'",
            code="UNKNOWN_JOB_TYPE", retryable_hint=False,
        )
        log.error("job=%s type=%s no handler registered", job.id[:8], job.job_type)
        await mirror.upsert_job(job, str(Status.FAILED))
        return

    try:
        result = await handler(job.payload)
    except JobFailure as failure:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        verdict = classify(
            code=failure.code,
            status_code=failure.status_code,
            retryable_hint=failure.retryable_hint,
        )
        status, delay = await consumer.nack(
            job, error=failure.message, code=failure.code,
            status_code=failure.status_code, retryable_hint=failure.retryable_hint,
        )

        await mirror.record_attempt(
            job, status=str(Status.FAILED), worker_id=consumer.worker_id,
            duration_ms=elapsed_ms,
            error_class="RETRYABLE" if verdict.retryable else "PERMANENT",
            error_type=failure.code or "UNKNOWN", error_message=failure.message,
            backoff_ms=int(delay * 1000) if delay else None,
        )
        await mirror.upsert_job(job, str(status))
        await mirror.record_failure(
            job, service=failure.service,
            error_class="RETRYABLE" if verdict.retryable else "PERMANENT",
            error_type=failure.code or "UNKNOWN", error_message=failure.message,
            resolution={
                Status.RETRY_WAIT: "RETRYING",
                Status.DEAD_LETTER: "DEAD_LETTER",
                Status.FAILED: "OPEN",
            }.get(status, "OPEN"),
        )

        if status is Status.RETRY_WAIT:
            log.warning(
                "job=%s type=%s attempt=%d/%d FAILED (%s) — retrying in %.1fs [%s]",
                job.id[:8], job.job_type, job.attempt, job.max_attempts,
                failure.code, delay, verdict.reason,
            )
        elif status is Status.DEAD_LETTER:
            log.error(
                "job=%s type=%s attempts exhausted (%d) — dead-lettered: %s",
                job.id[:8], job.job_type, job.attempt, failure.message,
            )
        else:
            log.error(
                "job=%s type=%s PERMANENT failure, not retrying: %s [%s]",
                job.id[:8], job.job_type, failure.message, verdict.reason,
            )
        return

    except Exception as exc:  # noqa: BLE001 - an unexpected bug must not kill the worker
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        status, delay = await consumer.nack(
            job, error=f"unhandled {type(exc).__name__}: {exc}", code="HANDLER_CRASH"
        )
        await mirror.record_attempt(
            job, status=str(Status.FAILED), worker_id=consumer.worker_id,
            duration_ms=elapsed_ms, error_class="RETRYABLE",
            error_type="HANDLER_CRASH", error_message=str(exc),
            backoff_ms=int(delay * 1000) if delay else None,
        )
        await mirror.upsert_job(job, str(status))
        log.exception("job=%s crashed in handler", job.id[:8])
        return

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    await consumer.ack(job, result=str(result))
    await mirror.record_attempt(
        job, status=str(Status.SUCCESS), worker_id=consumer.worker_id, duration_ms=elapsed_ms
    )
    await mirror.upsert_job(job, str(Status.SUCCESS))
    log.info(
        "job=%s type=%s priority=%s attempt=%d OK in %dms",
        job.id[:8], job.job_type, job.priority, job.attempt, elapsed_ms,
    )


async def worker_loop(index: int) -> None:
    consumer = QueueConsumer(
        settings.redis_url,
        namespace=settings.queue_namespace,
        worker_id=f"{os.uname().nodename}-{os.getpid()}-{index}",
        visibility_timeout=settings.visibility_timeout_sec,
        backoff_base=settings.backoff_base_sec,
        backoff_cap=settings.backoff_max_sec,
    )
    log.info("worker %d ready (%s)", index, consumer.worker_id)

    try:
        while not _shutdown.is_set():
            job = await consumer.reserve()
            if job is None:
                await asyncio.sleep(settings.poll_interval_sec)
                continue
            await process(consumer, job)
    finally:
        await consumer.close()


async def scheduler_loop() -> None:
    consumer = QueueConsumer(
        settings.redis_url,
        namespace=settings.queue_namespace,
        worker_id="scheduler",
        visibility_timeout=settings.visibility_timeout_sec,
    )
    log.info("scheduler ready (every %.1fs)", settings.scheduler_interval_sec)

    try:
        while not _shutdown.is_set():
            promoted = await consumer.promote_due()
            recovered = await consumer.reap_expired()
            if promoted:
                log.info("promoted %d due job(s)", promoted)
            if recovered:
                # Always worth a line: this means a worker died holding work.
                log.warning("recovered %d job(s) from expired leases", recovered)
            await asyncio.sleep(settings.scheduler_interval_sec)
    finally:
        await consumer.close()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=["worker", "scheduler", "both"], default="both")
    parser.add_argument("--concurrency", type=int, default=settings.worker_concurrency)
    args = parser.parse_args()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown.set)

    await mirror.open_pool()

    tasks: list[asyncio.Task] = []
    if args.role in ("worker", "both"):
        tasks += [asyncio.create_task(worker_loop(i)) for i in range(args.concurrency)]
    if args.role in ("scheduler", "both"):
        tasks.append(asyncio.create_task(scheduler_loop()))

    log.info("running role=%s concurrency=%d", args.role, args.concurrency)
    try:
        await _shutdown.wait()
        log.info("shutting down — letting in-flight jobs finish")
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        await mirror.close_pool()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
