# Database

Plain Postgres. No ORM, no migration framework — just numbered `.sql` files
in `migrations/`, applied in order by `migrate.py`. This keeps the database
readable and portable: anyone (including a future non-Python tool) can read
`001_init.sql` and know exactly what the schema is.

## Applying migrations

```bash
export DATABASE_URL=postgres://user:password@host:5432/dbname
python3 db/migrate.py
```

Safe to re-run — already-applied files are skipped (tracked in a
`schema_migrations` table it creates for itself).

## Adding a new migration later

Add a new file named `002_<short_description>.sql`, write plain SQL, run
`migrate.py` again. Never edit an already-applied migration file — add a
new one instead, the same rule every migration tool uses, so that what's
in the repo always matches what actually ran against real data.

## What's in Stage 0

See `001_init.sql` for the full schema and the reasoning behind each
choice (inline comments). Tables: `memories`, `tasks` (unused until Stage 1,
created now so no future migration has to touch it while it holds real
rows), `audit_log`, `approvals` (seeded with the owner's approval defaults),
`system_control` (holds the Emergency Stop flag).
