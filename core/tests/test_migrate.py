"""Applying the schema at startup.

The owner has no terminal, so a deploy that needs a manual SQL step is a
deploy that gets forgotten. These tests cover the two situations that
actually occur -- a fresh database, and one part-way through -- plus the
failure that must not take JARVIS down with it.
"""
from types import SimpleNamespace

import asyncpg
import pytest
import pytest_asyncio

from app import db as db_module
from app.migrate import apply_pending


@pytest_asyncio.fixture
async def blank(db_pool):
    """An empty schema, created and dropped per test.

    A schema rather than a database: creating databases needs a privilege
    an ordinary application role does not have, and requiring one would
    make these tests pass on my machine and fail on anyone else's. The
    migrations do not name a schema, so they land wherever search_path
    points -- which is exactly what makes this work.
    """
    import os
    import uuid

    name = f"migrate_test_{uuid.uuid4().hex[:8]}"
    async with db_pool.acquire() as conn:
        await conn.execute(f'CREATE SCHEMA "{name}"')

    async def init(conn):
        await db_module.init_connection(conn)
        await conn.execute(f'SET search_path TO "{name}"')

    dsn = os.environ.get(
        "DATABASE_URL", "postgres://jarvis:jarvis@localhost:5432/jarvis"
    )
    # The try starts BEFORE the pool is built. A fixture that fails during
    # setup never reaches its teardown, so an earlier version of this left
    # schemas behind every time it broke -- and the debris then made the
    # next run fail for a different reason, which cost more time to
    # untangle than the original fault.
    pool = None
    try:
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2, init=init)
        # asyncpg's Pool uses __slots__, so the schema name travels
        # alongside it rather than being attached to it.
        yield SimpleNamespace(pool=pool, schema=name)
    finally:
        if pool is not None:
            await pool.close()
        async with db_pool.acquire() as conn:
            await conn.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')


async def _tables(blank) -> set[str]:
    rows = await blank.pool.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname = $1", blank.schema
    )
    return {r["tablename"] for r in rows}


@pytest.mark.asyncio
async def test_a_fresh_database_gets_the_whole_schema(blank):
    applied = await apply_pending(blank.pool)

    assert applied == ["001_init.sql", "002_agent_foundation.sql"], (
        "migrations must run in filename order"
    )
    tables = await _tables(blank)
    for expected in ("memories", "tasks", "audit_log", "approvals",
                     "system_control", "agents", "workflows",
                     "agent_events", "agent_metrics"):
        assert expected in tables, f"{expected} is missing"


@pytest.mark.asyncio
async def test_only_what_is_missing_is_applied(blank):
    """The owner's actual situation: 001 applied by hand, 002 pending."""
    await apply_pending(blank.pool)

    async with blank.pool.acquire() as conn:
        await conn.execute("DROP TABLE agent_metrics, agent_events, "
                           "workflows, agents CASCADE")
        await conn.execute(
            "DELETE FROM schema_migrations WHERE filename = '002_agent_foundation.sql'"
        )

    applied = await apply_pending(blank.pool)

    assert applied == ["002_agent_foundation.sql"], "001 must not be re-run"
    assert "agents" in await _tables(blank)


@pytest.mark.asyncio
async def test_running_twice_changes_nothing(blank):
    """Every start calls this. It must be dull the second time."""
    await apply_pending(blank.pool)
    assert await apply_pending(blank.pool) == []


@pytest.mark.asyncio
async def test_a_broken_migration_rolls_back_and_stops(blank, tmp_path, monkeypatch):
    """One transaction per file, and no ploughing on afterwards.

    A half-applied schema is worse than an unapplied one, and applying
    later migrations over a broken one turns a single problem into
    several.
    """
    good = tmp_path / "001_good.sql"
    good.write_text("CREATE TABLE alpha (id INT);")
    bad = tmp_path / "002_bad.sql"
    bad.write_text("CREATE TABLE beta (id INT); SELECT this_is_not_valid();")
    later = tmp_path / "003_later.sql"
    later.write_text("CREATE TABLE gamma (id INT);")

    monkeypatch.setattr("app.migrate._migrations_dir", lambda: tmp_path)
    applied = await apply_pending(blank.pool)

    tables = await _tables(blank)
    assert applied == ["001_good.sql"]
    assert "alpha" in tables
    assert "beta" not in tables, "a failed migration must roll back entirely"
    assert "gamma" not in tables, "later migrations must not run over a failure"


@pytest.mark.asyncio
async def test_a_missing_directory_is_survivable(blank, monkeypatch):
    """JARVIS should still start and talk, even if it cannot find its schema."""
    monkeypatch.setattr("app.migrate._migrations_dir", lambda: None)
    assert await apply_pending(blank.pool) == []
