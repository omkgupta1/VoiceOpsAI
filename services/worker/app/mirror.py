"""
Mirroring queue state into PostgreSQL.

Redis holds the live queue; Postgres holds the history Redis deliberately
forgets. A job that succeeded on its fourth attempt leaves nothing behind in
Redis to say so, and "how often does the flight service make us retry" is
exactly the question the dashboard and the Phase 12 alarms need answered.

Mirroring is best-effort. A database hiccup must never cost a job: the queue is
the system of record for *work*, Postgres is the system of record for *history*,
and losing a history row is survivable in a way that losing a cancellation is not.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Json
from psycopg_pool import AsyncConnectionPool

from app.config import settings
from voiceops_queue import Job

log = logging.getLogger("worker.mirror")

_pool: AsyncConnectionPool | None = None


async def open_pool() -> None:
    global _pool
    _pool = AsyncConnectionPool(
        settings.database_url, min_size=1, max_size=10,
        kwargs={"row_factory": dict_row}, open=False,
    )
    await _pool.open(wait=True, timeout=10)


async def close_pool() -> None:
    if _pool is not None:
        await _pool.close()


@asynccontextmanager
async def _connection():
    if _pool is None:
        raise RuntimeError("Connection pool is not open")
    async with _pool.connection() as conn:
        yield conn


async def _safe(description: str, coro) -> None:
    try:
        await coro
    except Exception as exc:  # noqa: BLE001 - history must never break the queue
        log.warning("could not mirror %s: %s", description, exc)


async def upsert_job(job: Job, status: str) -> None:
    async def _run() -> None:
        async with _connection() as conn:
            await conn.execute(
                """
                INSERT INTO jobs (id, call_id, customer_id, job_type, priority, status,
                                  payload, idempotency_key, scheduled_at, attempt_count,
                                  max_attempts, last_error)
                VALUES (%s,%s,%s,%s,%s::job_priority,%s::job_status,%s,%s,
                        to_timestamp(%s),%s,%s,%s)
                ON CONFLICT (id) DO UPDATE SET
                    status        = EXCLUDED.status,
                    attempt_count = EXCLUDED.attempt_count,
                    last_error    = EXCLUDED.last_error,
                    completed_at  = CASE WHEN EXCLUDED.status IN ('SUCCESS','FAILED','DEAD_LETTER')
                                         THEN now() ELSE jobs.completed_at END
                """,
                (
                    job.id, job.call_id, job.customer_id, job.job_type,
                    str(job.priority), status, Json(job.payload),
                    job.idempotency_key, job.run_at, job.attempt,
                    job.max_attempts, job.last_error,
                ),
            )
    await _safe(f"job {job.id}", _run())


async def record_attempt(
    job: Job, *, status: str, worker_id: str, duration_ms: int,
    error_class: str | None = None, error_type: str | None = None,
    error_message: str | None = None, backoff_ms: int | None = None,
) -> None:
    async def _run() -> None:
        async with _connection() as conn:
            await conn.execute(
                """
                INSERT INTO job_attempts (job_id, attempt_number, status, worker_id,
                                          finished_at, duration_ms, error_class,
                                          error_type, error_message, backoff_ms)
                VALUES (%s,%s,%s::job_status,%s,now(),%s,%s::error_class,%s,%s,%s)
                ON CONFLICT (job_id, attempt_number) DO NOTHING
                """,
                (
                    job.id, job.attempt, status, worker_id, duration_ms,
                    error_class, error_type, error_message, backoff_ms,
                ),
            )
    await _safe(f"attempt {job.id}#{job.attempt}", _run())


async def record_failure(
    job: Job, *, service: str, error_class: str, error_type: str,
    error_message: str, resolution: str,
) -> None:
    async def _run() -> None:
        async with _connection() as conn:
            await conn.execute(
                """
                INSERT INTO failures (job_id, call_id, service, error_class, error_type,
                                      error_message, retry_count, resolution_status, context)
                VALUES (%s,%s,%s,%s::error_class,%s,%s,%s,%s::resolution_status,%s)
                """,
                (
                    job.id, job.call_id, service, error_class, error_type,
                    error_message[:2000], job.attempt, resolution,
                    Json({"job_type": job.job_type}),
                ),
            )
    await _safe(f"failure {job.id}", _run())


async def fetch_one(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    async with _connection() as conn:
        cur = await conn.execute(sql, params)
        return await cur.fetchone()
