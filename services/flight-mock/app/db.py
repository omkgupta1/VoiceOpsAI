"""
Async PostgreSQL access.

Raw SQL over psycopg3 rather than an ORM — consistent with ADR 0002, and for a
service this small an ORM would add indirection without removing any work.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import settings

_pool: AsyncConnectionPool | None = None


async def open_pool() -> None:
    global _pool
    _pool = AsyncConnectionPool(
        settings.database_url,
        min_size=1,
        max_size=10,
        kwargs={"row_factory": dict_row},
        open=False,
    )
    await _pool.open(wait=True, timeout=10)


async def close_pool() -> None:
    if _pool is not None:
        await _pool.close()


@asynccontextmanager
async def connection():
    if _pool is None:
        raise RuntimeError("Connection pool is not open")
    async with _pool.connection() as conn:
        yield conn


async def fetch_one(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    async with connection() as conn:
        cur = await conn.execute(sql, params)
        return await cur.fetchone()


async def fetch_all(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    async with connection() as conn:
        cur = await conn.execute(sql, params)
        return await cur.fetchall()


async def execute(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    """Run a statement and return its RETURNING row, if any."""
    async with connection() as conn:
        cur = await conn.execute(sql, params)
        return await cur.fetchone() if cur.description else None
