"""Applying pending schema migrations at startup.

Why this exists: the owner works from an iPad and has no terminal. Every
schema change so far has meant finding a dashboard, finding a SQL editor,
copying a file and pasting it — friction that has now interrupted the
work twice, and that scales badly as migrations accumulate.

JARVIS knows which migrations exist and which the database has. It should
just apply them.

Three things keep that safe:

* An advisory lock, so two containers starting at once cannot both apply
  the same migration. Render replaces an instance before stopping the old
  one, so overlapping starts are normal rather than exotic.
* One transaction per file. A migration either lands whole or not at all;
  a half-applied schema is far worse than an unapplied one.
* A failure is loud but not fatal. Conversation, memory and voice run on
  the schema that is already there, and taking those away to protest a
  missing table would punish the owner for a problem they did not cause.
"""
import logging
import os
from pathlib import Path

import asyncpg

logger = logging.getLogger("jarvis.migrate")

# Any constant will do; it only has to be the same in every instance.
_LOCK_KEY = 8_314_559_201

# The schema state, once it is known for certain. See `state()`.
_CACHED_STATE: dict | None = None


def _migrations_dir() -> Path | None:
    """Find the migrations, in the container and in a checkout.

    The container has them at /app/db/migrations; a repository checkout
    has them one level further up. Looking in both is two lines and saves
    a class of "works locally, not deployed" that is tedious to diagnose.
    """
    here = Path(__file__).resolve().parent
    for candidate in (here.parent / "db" / "migrations",
                      here.parent.parent / "db" / "migrations"):
        if candidate.is_dir():
            return candidate
    return None


async def apply_pending(pool: asyncpg.Pool) -> list[str]:
    """Apply every migration the database has not seen. Returns their names."""
    directory = _migrations_dir()
    if directory is None:
        logger.warning(
            "No migrations directory found, so the schema cannot be checked. "
            "If tables are missing, apply db/migrations/*.sql by hand."
        )
        return []

    files = sorted(directory.glob("*.sql"))
    if not files:
        return []

    applied: list[str] = []
    async with pool.acquire() as conn:
        # Held for the whole run and released with the connection, so a
        # crash mid-migration cannot leave the lock stuck.
        await conn.execute("SELECT pg_advisory_lock($1)", _LOCK_KEY)
        try:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    filename    TEXT PRIMARY KEY,
                    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            done = {
                r["filename"]
                for r in await conn.fetch("SELECT filename FROM schema_migrations")
            }

            for path in files:
                if path.name in done:
                    continue
                logger.info("Applying migration %s", path.name)
                try:
                    async with conn.transaction():
                        await conn.execute(path.read_text())
                        await conn.execute(
                            "INSERT INTO schema_migrations (filename) VALUES ($1)",
                            path.name,
                        )
                    applied.append(path.name)
                except Exception as exc:  # noqa: BLE001
                    # Stop at the first failure: later migrations may
                    # depend on this one, and applying them over a broken
                    # schema turns one problem into several.
                    logger.error(
                        "Migration %s FAILED and was rolled back: %s. JARVIS "
                        "will keep running on the schema it already has, but "
                        "anything needing this migration will not work.",
                        path.name, exc,
                    )
                    break
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", _LOCK_KEY)

    if applied:
        logger.info("Applied %d migration(s): %s", len(applied), ", ".join(applied))
    return applied


async def state(pool: asyncpg.Pool) -> dict:
    """What the schema actually is, right now, on this deployment.

    This exists because "did the migration land?" turned out to be
    genuinely hard to answer from an iPad. The migration log line is
    written once, on the one startup that applies something, and is gone
    by the next deploy -- so the question outlives its own evidence.

    Reported rather than logged, so it can be read at any time.

    Deliberately says nothing about the database itself: no host, no
    connection string, no raw exception text, because this is served
    unauthenticated. Migration filenames and a commit hash are already
    public in the repository; a failure reason might not be.
    """
    global _CACHED_STATE
    if _CACHED_STATE is not None:
        return _CACHED_STATE

    directory = _migrations_dir()
    info: dict = {
        "commit": (os.environ.get("RENDER_GIT_COMMIT")
                   or os.environ.get("GIT_COMMIT") or "")[:7] or None,
        "migrations_in_image": None,
        "applied": None,
        "pending": None,
        "state": "unknown",
    }

    if directory is None:
        # The container was built without the schema in it. Nothing is
        # broken with the database; the image simply cannot see what it
        # is supposed to apply.
        info["state"] = "no_migrations_in_image"
        info["migrations_in_image"] = 0
        _CACHED_STATE = info
        return info

    files = [p.name for p in sorted(directory.glob("*.sql"))]
    info["migrations_in_image"] = len(files)

    try:
        rows = await pool.fetch("SELECT filename FROM schema_migrations")
        done = {r["filename"] for r in rows}
    except Exception:  # noqa: BLE001 - health must answer even when the DB will not
        info["state"] = "unreadable"
        return info

    pending = [f for f in files if f not in done]
    info["applied"] = sorted(done)
    info["pending"] = pending
    info["state"] = "up_to_date" if not pending else "pending"

    # Cached because it cannot change again: migrations are applied at
    # startup and nowhere else, so this process will report the same
    # answer for its whole life. /health is polled continuously by the
    # host, and a diagnostic that bills a query per poll would be a
    # strange thing to add to a system with a monthly budget.
    #
    # Only settled answers are kept. "unreadable" is a statement about
    # one moment, and caching it would turn a blip into a permanent lie.
    _CACHED_STATE = info
    return info
