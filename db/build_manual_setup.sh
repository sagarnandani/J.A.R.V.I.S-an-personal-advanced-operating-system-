#!/usr/bin/env bash
# Regenerates db/manual_setup.sql from the migration files.
#
# Why this exists: applying migrations normally means running
# `python3 db/migrate.py` from a computer with Python and network access to
# the database. The owner works primarily from an iPad, and Supabase's
# dashboard has a SQL editor that runs pasted SQL directly -- no local
# setup needed. This bundles every migration into one paste-able file and
# records them in `schema_migrations` exactly as migrate.py would, so the
# two paths stay interchangeable and nothing gets applied twice later.
#
# Run this after adding any new migration:
#   bash db/build_manual_setup.sh
set -euo pipefail
cd "$(dirname "$0")"

OUT=manual_setup.sql

{
  echo "-- GENERATED FILE -- do not edit by hand."
  echo "-- Regenerate with: bash db/build_manual_setup.sh"
  echo "--"
  echo "-- Paste the whole of this file into the Supabase dashboard's SQL"
  echo "-- Editor and press Run. It applies the full JARVIS schema and marks"
  echo "-- each migration as applied, so running db/migrate.py later will"
  echo "-- correctly skip them instead of trying to apply them twice."
  echo
  echo "BEGIN;"
  echo
  echo "CREATE TABLE IF NOT EXISTS schema_migrations ("
  echo "    filename    TEXT PRIMARY KEY,"
  echo "    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()"
  echo ");"
  echo

  for f in migrations/*.sql; do
    name="$(basename "$f")"
    echo "-- ================================================================"
    echo "-- $name"
    echo "-- ================================================================"
    cat "$f"
    echo
    echo "INSERT INTO schema_migrations (filename) VALUES ('$name')"
    echo "    ON CONFLICT (filename) DO NOTHING;"
    echo
  done

  echo "COMMIT;"
} > "$OUT"

echo "Wrote $OUT ($(wc -l < "$OUT") lines)."
