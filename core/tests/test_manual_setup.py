"""The second, partial definition of the schema, and the trap in it.

`db/manual_setup.sql` exists because docker-compose hands it to Postgres
as an init script on an empty database, and because the Supabase
migration path in docs/SELF_HOSTING.md uses it. It is not generated from
the migrations -- it is maintained by hand, and it is already behind
them.

Being behind is safe. The application applies every migration it has not
seen at startup, so a fresh `docker compose up` ends with the full
schema whatever this file contains. That is verified end to end
elsewhere; it is not what these tests are for.

What is NOT safe is this file **claiming** a migration it does not
contain. It writes rows into `schema_migrations`, and the migration
runner skips anything already listed there. So a claim without the
matching tables means the runner skips that migration, the tables never
exist, and nothing complains until something queries one -- in
production, on a machine with no test suite, at the moment the owner is
trying to use it.

These tests do not require the file to be complete. They require it to
be honest about what it is.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SETUP = ROOT / "db" / "manual_setup.sql"
MIGRATIONS = ROOT / "db" / "migrations"


def _claimed() -> list[str]:
    """The migrations this file tells the runner not to bother with."""
    return re.findall(
        r"INSERT INTO schema_migrations \(filename\) VALUES \('([^']+)'\)",
        SETUP.read_text(),
    )


def _tables_in(sql: str) -> set[str]:
    return {
        name.lower() for name in
        re.findall(r"CREATE TABLE (?:IF NOT EXISTS )?(\w+)", sql, re.I)
    }


def test_it_only_claims_migrations_that_exist():
    for filename in _claimed():
        assert (MIGRATIONS / filename).exists(), (
            f"manual_setup.sql marks '{filename}' as applied, but there is "
            f"no such migration -- the runner will never reconcile that"
        )


def test_every_migration_it_claims_is_one_it_actually_contains():
    """The trap this file exists to guard.

    A row in schema_migrations makes the runner skip that migration. If
    the row is there and the tables are not, they are never created, and
    the failure surfaces as a missing relation at runtime rather than as
    anything that looks like a schema problem.
    """
    present = _tables_in(SETUP.read_text())
    for filename in _claimed():
        for table in _tables_in((MIGRATIONS / filename).read_text()):
            assert table in present, (
                f"manual_setup.sql claims '{filename}' is applied but never "
                f"creates '{table}'. On a fresh docker compose install the "
                f"runner will skip that migration and '{table}' will not "
                f"exist -- discovered in production, as a missing relation."
            )


def test_it_does_not_claim_migrations_it_has_fallen_behind():
    """Falling behind is fine. Claiming to be current is not.

    This is what stops the file from being 'fixed' by adding rows rather
    than tables -- the cheap fix that makes the drift invisible and the
    failure worse.
    """
    claimed = set(_claimed())
    every = {path.name for path in MIGRATIONS.glob("*.sql")}
    behind = sorted(every - claimed)

    present = _tables_in(SETUP.read_text())
    for filename in behind:
        created = _tables_in((MIGRATIONS / filename).read_text())
        # If it creates the tables, it must say so; if it says nothing, it
        # must not create them. Anything else is a half-claim.
        if created and created <= present:
            pytest.fail(
                f"manual_setup.sql creates everything in '{filename}' but "
                f"does not record it as applied, so the runner will run it "
                f"again. Harmless only while every migration is written "
                f"with IF NOT EXISTS."
            )


@pytest.mark.parametrize("filename", sorted(
    p.name for p in (Path(__file__).resolve().parents[2]
                     / "db" / "migrations").glob("*.sql")))
def test_every_migration_can_be_applied_twice(filename):
    """What makes the drift survivable.

    The application re-applies anything not recorded, and an operator
    running a migration by hand is a normal thing to do. Every CREATE
    must therefore be IF NOT EXISTS, and every ALTER TABLE ... ADD COLUMN
    likewise -- otherwise the second run fails half way and leaves the
    schema in a state nobody designed.
    """
    sql = (MIGRATIONS / filename).read_text()

    # The lookahead is the whole test. Without it, "CREATE TABLE IF NOT
    # EXISTS agents" matches with the table name captured as "IF", and
    # every migration fails for a reason that has nothing to do with
    # idempotency.
    for match in re.finditer(
            r"CREATE (TABLE|INDEX|UNIQUE INDEX)\s+(?!IF NOT EXISTS)(\w+)",
            sql, re.I):
        pytest.fail(
            f"{filename}: 'CREATE {match.group(1)} {match.group(2)}' is not "
            f"IF NOT EXISTS, so applying it twice fails"
        )

    for match in re.finditer(r"ADD COLUMN\s+(?!IF NOT EXISTS)(\w+)", sql, re.I):
        pytest.fail(
            f"{filename}: 'ADD COLUMN {match.group(1)}' is not IF NOT "
            f"EXISTS, so applying it twice fails"
        )
