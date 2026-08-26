"""Shared test fixtures.

`db_pool` spins the app's real connection pool up against DATABASE_URL and
tears it down after the test. Integration tests that need it should accept
this fixture; if no reachable Postgres is configured, they're skipped
rather than failed, so `pytest` still passes in an environment with no
database (e.g. a plain CI checkout with nothing provisioned yet).
"""
import os

import asyncpg
import pytest
import pytest_asyncio

from app import db as db_module


def _database_url() -> str:
    return os.environ.get(
        "DATABASE_URL", "postgres://jarvis:jarvis@localhost:5432/jarvis"
    )


@pytest_asyncio.fixture
async def db_pool():
    url = _database_url()
    try:
        pool = await asyncpg.create_pool(url, min_size=1, max_size=2)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"No reachable Postgres at {url}: {exc}")
        return

    db_module._pool = pool
    try:
        yield pool
    finally:
        await pool.close()
        db_module._pool = None
