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


@pytest.fixture(autouse=True)
def _forget_the_cached_schema_state(monkeypatch):
    """The schema report is cached for the process's life, deliberately.

    That is right in production -- one process, migrations applied once at
    startup -- and wrong across tests, where each one builds a different
    schema in the same process. Without this the second test reads the
    first one's answer, which is exactly how these passed alone and failed
    together.
    """
    import app.migrate as migrate

    monkeypatch.setattr(migrate, "_CACHED_STATE", None)


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
        # search_path travels as a connection parameter, not as a SET in
        # `init`. asyncpg resets a connection when it goes back to the
        # pool, which wipes anything init ran -- so a SET survives only
        # for as long as one caller holds the connection. `apply_pending`
        # holds one for its whole run and looked isolated; anything using
        # `pool.fetch()` was quietly reading the shared public schema
        # instead, and one test here passed because of what a previous
        # real run had left there. A startup parameter survives the reset.
        pool = await asyncpg.create_pool(
            dsn, min_size=1, max_size=2, init=init,
            server_settings={"search_path": name},
        )
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

    assert applied == [
        "001_init.sql", "002_agent_foundation.sql", "003_schedules.sql",
        "004_money.sql", "005_approvals_and_shadow_cost.sql",
    ], "migrations must run in filename order"
    tables = await _tables(blank)
    for expected in ("memories", "tasks", "audit_log", "approvals", "schedules", "money_events", "task_approvals",
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


# --- reporting the schema, rather than logging it once ---------------------

@pytest.mark.asyncio
async def test_the_schema_reports_itself_as_up_to_date(blank):
    """The question "did the migration land?" outlives its own log line.

    "Applied 1 migration(s)" is written on the one startup that applies
    something and is gone by the next deploy. This is the same answer,
    available at any time.
    """
    from app.migrate import state

    await apply_pending(blank.pool)
    reported = await state(blank.pool)

    assert reported["state"] == "up_to_date"
    assert reported["pending"] == []
    assert "002_agent_foundation.sql" in reported["applied"]
    assert reported["migrations_in_image"] >= 2


@pytest.mark.asyncio
async def test_a_half_applied_schema_says_what_is_missing(blank):
    """The case that actually happened, and by name."""
    from app.migrate import state

    await apply_pending(blank.pool)
    await blank.pool.execute(
        "DELETE FROM schema_migrations WHERE filename = '002_agent_foundation.sql'"
    )

    reported = await state(blank.pool)
    assert reported["state"] == "pending"
    assert reported["pending"] == ["002_agent_foundation.sql"]


@pytest.mark.asyncio
async def test_an_image_built_without_the_schema_says_so(blank, monkeypatch):
    """The failure that is invisible from the database side.

    Nothing is wrong with Postgres; the container simply does not contain
    the files it is meant to apply. Told apart from "pending" because the
    fix is a rebuild, not a retry.
    """
    from app.migrate import state

    monkeypatch.setattr("app.migrate._migrations_dir", lambda: None)
    reported = await state(blank.pool)

    assert reported["state"] == "no_migrations_in_image"
    assert reported["migrations_in_image"] == 0


@pytest.mark.asyncio
async def test_an_unreachable_database_is_reported_not_raised(blank, monkeypatch):
    """/health must answer even when the thing it describes will not."""
    from app.migrate import state

    class Dead:
        async def fetch(self, *args):
            raise RuntimeError("connection refused")

    reported = await state(Dead())
    assert reported["state"] == "unreadable"


@pytest.mark.asyncio
async def test_nothing_about_the_database_itself_is_disclosed(blank, monkeypatch):
    """/health is public, so this reports the schema and nothing around it.

    Filenames and a commit hash are already public in the repository. A
    connection string or a raw exception is not, and an unauthenticated
    endpoint is exactly the wrong place to find out otherwise.
    """
    from app.migrate import state

    class Leaky:
        async def fetch(self, *args):
            raise RuntimeError(
                "could not connect: postgres://jarvis:hunter2@db.internal:5432/jarvis"
            )

    blob = repr(await state(Leaky()))
    assert "hunter2" not in blob and "db.internal" not in blob


@pytest.mark.asyncio
async def test_a_settled_answer_is_not_re_queried(blank, monkeypatch):
    """/health is polled continuously; the answer cannot change until restart.

    Migrations run at startup and nowhere else, so this process reports
    the same thing for its whole life. Billing a query per health check
    for a fixed answer would be a strange thing to add to a system with a
    monthly budget.
    """
    import app.migrate as migrate

    await apply_pending(blank.pool)

    class Counting:
        """A wrapper, because asyncpg's Pool uses __slots__ and cannot be
        patched in place."""

        def __init__(self, pool):
            self.pool, self.queries = pool, 0

        async def fetch(self, *args, **kwargs):
            self.queries += 1
            return await self.pool.fetch(*args, **kwargs)

    counted = Counting(blank.pool)
    first = await migrate.state(counted)
    for _ in range(5):
        await migrate.state(counted)
    queries = counted.queries

    assert first["state"] == "up_to_date"
    assert queries == 1, f"the schema was queried {queries} times for a fixed answer"


@pytest.mark.asyncio
async def test_a_momentary_failure_is_never_cached(blank, monkeypatch):
    """Caching 'unreadable' would turn one bad moment into a permanent lie."""
    import app.migrate as migrate

    await apply_pending(blank.pool)

    class Blip:
        async def fetch(self, *args):
            raise RuntimeError("connection reset")

    assert (await migrate.state(Blip()))["state"] == "unreadable"
    assert (await migrate.state(blank.pool))["state"] == "up_to_date"
