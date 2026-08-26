-- Stage 0 schema: memories, tasks, audit_log, approvals, system_control
--
-- Design notes (plain language):
-- * We use TEXT + CHECK constraints instead of native Postgres ENUM types.
--   Enums are annoying to extend later (adding a value is a schema migration
--   with its own quirks); a CHECK constraint gives the same safety and is a
--   one-line change to widen later. This is the kind of "boring, portable"
--   choice the architecture doc asks for.
-- * `tasks`, and the task-related fields, are not used by any Stage 0 logic
--   yet -- they're created now so Stage 1 (task engine) doesn't need a
--   migration that could conflict with real data already in the other
--   tables.
-- * `system_control` is not in the original brief's table list. It's a tiny
--   single-purpose table holding the Emergency Stop flag (architecture doc,
--   section F: "build this early, not eventually"). Stage 0 has no agents
--   or autonomous actions to stop yet, but the flag and the check are wired
--   through the API now so nothing needs retrofitting later.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS memories (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content             TEXT NOT NULL,
    category            TEXT NOT NULL CHECK (category IN (
                            'working', 'episodic', 'semantic', 'project',
                            'people', 'decision', 'task', 'preference', 'system'
                        )),
    origin              TEXT NOT NULL CHECK (origin IN (
                            'stated', 'retrieved', 'inferred', 'predicted'
                        )),
    confidence          NUMERIC(3,2) NOT NULL DEFAULT 1.0
                            CHECK (confidence >= 0 AND confidence <= 1),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at          TIMESTAMPTZ,
    related_memory_ids  UUID[] NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_memories_category ON memories (category);

CREATE TABLE IF NOT EXISTS tasks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    objective       TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
                        'pending', 'in_progress', 'completed', 'failed', 'awaiting_approval'
                    )),
    origin          TEXT,
    assigned_agent  TEXT,
    budget_tokens   INTEGER,
    budget_cost     NUMERIC(10,4),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    checkpoint_data JSONB
);

CREATE TABLE IF NOT EXISTS audit_log (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor        TEXT NOT NULL,
    action       TEXT NOT NULL,
    category     TEXT NOT NULL CHECK (category IN ('low_risk', 'medium_risk', 'high_risk')),
    approved_by  TEXT,
    outcome      TEXT,
    cost         NUMERIC(10,6),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log (created_at DESC);

CREATE TABLE IF NOT EXISTS approvals (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action_category  TEXT NOT NULL UNIQUE,
    default_policy   TEXT NOT NULL CHECK (default_policy IN (
                          'auto', 'ask_every_time', 'ask_above_threshold'
                      )),
    threshold_value  NUMERIC(10,4),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Owner's stated defaults (Stage 0 Build Brief, section 3).
INSERT INTO approvals (action_category, default_policy, threshold_value) VALUES
    ('research',                 'auto',            NULL),
    ('drafting',                 'auto',            NULL),
    ('publishing',               'ask_every_time',  NULL),
    ('spending',                 'ask_every_time',  NULL),
    ('credentials_or_security',  'ask_every_time',  NULL)
ON CONFLICT (action_category) DO NOTHING;

CREATE TABLE IF NOT EXISTS system_control (
    key         TEXT PRIMARY KEY,
    value       BOOLEAN NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO system_control (key, value) VALUES ('emergency_stop', false)
ON CONFLICT (key) DO NOTHING;
