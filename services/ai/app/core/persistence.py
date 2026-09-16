"""
Persisting calls and conversation turns.

Every turn is written to PostgreSQL as it happens, rather than at the end of the
call. A call that crashes mid-way is exactly the one worth being able to inspect
afterwards, and an in-memory buffer would lose precisely that.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Json
from psycopg_pool import AsyncConnectionPool

from app.config import settings

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


async def ping() -> bool:
    async with _connection() as conn:
        await conn.execute("SELECT 1")
    return True


async def create_call(*, channel: str = "browser", customer_id: str | None = None) -> str:
    async with _connection() as conn:
        row = await (
            await conn.execute(
                """
                INSERT INTO calls (customer_id, status, channel)
                VALUES (%s, 'IN_PROGRESS', %s)
                RETURNING id
                """,
                (customer_id, channel),
            )
        ).fetchone()
    return str(row["id"])


async def next_turn_index(call_id: str) -> int:
    async with _connection() as conn:
        row = await (
            await conn.execute(
                "SELECT coalesce(max(turn_index) + 1, 0) AS next FROM conversations WHERE call_id = %s",
                (call_id,),
            )
        ).fetchone()
    return int(row["next"])


async def record_turn(
    *,
    call_id: str,
    turn_index: int,
    speaker: str,
    message: str,
    intent: str | None = None,
    confidence: float | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    stt_ms: int | None = None,
    llm_ms: int | None = None,
    tts_ms: int | None = None,
    providers: dict[str, str] | None = None,
) -> None:
    async with _connection() as conn:
        await conn.execute(
            """
            INSERT INTO conversations (call_id, turn_index, speaker, message, intent,
                                       confidence, tool_calls, stt_ms, llm_ms, tts_ms, providers)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                call_id, turn_index, speaker, message, intent, confidence,
                Json(tool_calls or []), stt_ms, llm_ms, tts_ms, Json(providers or {}),
            ),
        )


async def update_call(
    *,
    call_id: str,
    intent: str | None = None,
    escalated: bool = False,
    escalation_reason: str | None = None,
) -> None:
    """
    Roll a completed turn up onto the call.

    `primary_intent` uses COALESCE so the first identified intent sticks: a call
    that starts as a cancellation and ends with an escalation is still, for
    reporting purposes, a cancellation call.
    """
    async with _connection() as conn:
        await conn.execute(
            """
            UPDATE calls
               SET primary_intent    = coalesce(primary_intent, %s),
                   status            = CASE WHEN %s THEN 'ESCALATED'::call_status ELSE status END,
                   escalation_status = CASE WHEN %s THEN 'ESCALATED'::escalation_status
                                            ELSE escalation_status END,
                   escalation_reason = coalesce(escalation_reason, %s)
             WHERE id = %s
            """,
            (intent, escalated, escalated, escalation_reason, call_id),
        )


async def end_call(*, call_id: str, status: str = "COMPLETED") -> None:
    async with _connection() as conn:
        await conn.execute(
            """
            UPDATE calls
               SET ended_at = now(),
                   status = CASE WHEN status = 'ESCALATED' THEN status ELSE %s::call_status END
             WHERE id = %s AND ended_at IS NULL
            """,
            (status, call_id),
        )


async def get_informed_bookings(call_id: str) -> set[str]:
    """
    Booking references whose details have been read back to the customer on this call.

    Kept on the call row rather than in process memory so the confirmation gate
    survives a restart. A gate that forgets on deploy is a gate that eventually
    cancels someone's booking without asking.
    """
    async with _connection() as conn:
        row = await (
            await conn.execute(
                "SELECT metadata -> 'informed_bookings' AS informed FROM calls WHERE id = %s",
                (call_id,),
            )
        ).fetchone()
    return set((row or {}).get("informed") or [])


async def set_informed_bookings(call_id: str, informed: set[str]) -> None:
    async with _connection() as conn:
        await conn.execute(
            """
            UPDATE calls
               SET metadata = jsonb_set(coalesce(metadata, '{}'::jsonb),
                                        '{informed_bookings}', %s::jsonb, true)
             WHERE id = %s
            """,
            (Json(sorted(informed)), call_id),
        )


async def load_history(call_id: str, limit: int = 20) -> list[dict[str, Any]]:
    async with _connection() as conn:
        rows = await (
            await conn.execute(
                """
                SELECT speaker, message FROM conversations
                WHERE call_id = %s ORDER BY turn_index DESC LIMIT %s
                """,
                (call_id, limit),
            )
        ).fetchall()
    return list(reversed(rows))
