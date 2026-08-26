"""Postgres connection pool, shared across the app via FastAPI's lifespan."""
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from fastapi import FastAPI

from app.config import get_settings

_pool: asyncpg.Pool | None = None


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
        settings.database_url, min_size=1, max_size=5
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
