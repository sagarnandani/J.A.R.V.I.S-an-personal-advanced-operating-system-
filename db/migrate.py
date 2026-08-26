#!/usr/bin/env python3
"""Tiny migration runner.

Why not a full migration framework (Alembic etc.)? At Stage 0 there is one
developer (an AI, working from plain-language briefs) and one database.
A framework adds a learning surface with no payoff yet. This script does
the one thing a migration runner needs to do: apply each .sql file in
`db/migrations/` in filename order, exactly once, tracked in a
`schema_migrations` table. It's ~40 lines and every line is inspectable.

Usage:
    DATABASE_URL=postgres://... python3 db/migrate.py
"""
import asyncio
import os
import sys
from pathlib import Path

import asyncpg

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


async def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL is not set.", file=sys.stderr)
        sys.exit(1)

    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename    TEXT PRIMARY KEY,
                applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )

        applied = {
            row["filename"]
            for row in await conn.fetch("SELECT filename FROM schema_migrations")
        }

        pending = sorted(
            p for p in MIGRATIONS_DIR.glob("*.sql") if p.name not in applied
        )

        if not pending:
            print("Nothing to do -- database is already up to date.")
            return

        for path in pending:
            print(f"Applying {path.name} ...")
            sql = path.read_text()
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (filename) VALUES ($1)",
                    path.name,
                )
            print(f"  done.")

        print(f"Applied {len(pending)} migration(s).")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
