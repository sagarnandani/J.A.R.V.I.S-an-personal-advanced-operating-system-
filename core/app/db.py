"""Postgres connection pool, shared across the app via FastAPI's lifespan."""
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from fastapi import FastAPI

from app.config import get_settings

_pool: asyncpg.Pool | None = None


def _pool_kwargs(database_url: str) -> dict:
    """Connection settings that depend on what kind of Postgres we're talking to.

    Managed Postgres is often reached through a connection pooler rather
    than directly. Supabase's "transaction pooler" (port 6543) multiplexes
    many clients onto few server connections, and as a side effect it can't
    support prepared statements -- which asyncpg uses by default. Left
    alone, that combination fails at runtime with a confusing
    "prepared statement does not exist" error partway through normal use,
    not at startup where it would be obvious.

    Detecting it here and turning the statement cache off means pasting
    either pooler URL just works. The session pooler (port 5432) has no
    such limitation, so it keeps the faster default.
    """
    is_transaction_pooler = ":6543" in database_url
    if is_transaction_pooler:
        return {"statement_cache_size": 0}
    return {}


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError(
            "Database pool not initialized. This means the app started "
            "without running its lifespan startup -- check main.py."
        )
    return _pool


@asynccontextmanager
async def lifespan_db(app: FastAPI):
    global _pool
    settings = get_settings()
    _pool = await asyncpg.create_pool(
        settings.database_url,
        min_size=1,
        max_size=5,
        **_pool_kwargs(settings.database_url),
    )
    try:
        yield
    finally:
        await _pool.close()
        _pool = None


async def fetch(query: str, *args: Any) -> list[asyncpg.Record]:
    async with get_pool().acquire() as conn:
        return await conn.fetch(query, *args)


async def fetchrow(query: str, *args: Any) -> asyncpg.Record | None:
    async with get_pool().acquire() as conn:
        return await conn.fetchrow(query, *args)


async def execute(query: str, *args: Any) -> str:
    async with get_pool().acquire() as conn:
        return await conn.execute(query, *args)
