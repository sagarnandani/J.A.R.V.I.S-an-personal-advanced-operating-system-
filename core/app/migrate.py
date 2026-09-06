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
from pathlib import Path

import asyncpg

logger = logging.getLogger("jarvis.migrate")

# Any constant will do; it only has to be the same in every instance.
_LOCK_KEY = 8_314_559_201


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
