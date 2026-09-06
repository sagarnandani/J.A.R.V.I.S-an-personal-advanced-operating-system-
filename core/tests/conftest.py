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


# Tests that touch memory empty the tables. That is fine against a
# scratch database and catastrophic against a real one -- running the
# suite with DATABASE_URL pointing at your own JARVIS would delete
# everything it remembers, with no warning and no undo.
#
# So the suite refuses to touch a database that does not look disposable.
# Getting this wrong the safe way costs a skipped test run and a one-line
# message saying how to proceed. Getting it wrong the other way costs the
# owner their memories.
_DISPOSABLE_HINTS = ("test", "jarvis_test", "localhost", "127.0.0.1")


def _looks_disposable(url: str) -> bool:
    if os.environ.get("JARVIS_ALLOW_DESTRUCTIVE_TESTS") == "yes-i-mean-it":
        return True
    # A hosted database is never disposable, whatever it is called.
    if any(host in url for host in ("supabase", "render.com", "amazonaws", "neon.tech")):
        return False
    return any(hint in url for hint in _DISPOSABLE_HINTS)


@pytest_asyncio.fixture
async def db_pool():
    url = _database_url()

    if not _looks_disposable(url):
        pytest.skip(
            "Refusing to run against a database that does not look "
            "disposable: these tests DELETE every memory. Point "
            "DATABASE_URL at a local or throwaway database, or set "
            "JARVIS_ALLOW_DESTRUCTIVE_TESTS=yes-i-mean-it if you are sure."
        )
        return

    try:
        # Same JSON decoding as the app, or tests read JSONB as strings
        # and pass against behaviour production never sees.
        pool = await asyncpg.create_pool(
            url, min_size=1, max_size=2, init=db_module.init_connection
        )
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"No reachable Postgres at {url}: {exc}")
        return

    db_module._pool = pool
    try:
        yield pool
    finally:
        await pool.close()
        db_module._pool = None


# A websocket test that waits for a message which never comes hangs the
# whole suite instead of failing it. Ten seconds is far longer than any
# test here legitimately needs.
def pytest_collection_modifyitems(items):
    for item in items:
        if "live_voice" in item.nodeid and item.get_closest_marker("timeout") is None:
            item.add_marker(pytest.mark.timeout(10))
