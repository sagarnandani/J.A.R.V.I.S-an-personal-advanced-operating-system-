-- Work that happens without being asked.
--
-- Until now JARVIS only acted when the owner typed or spoke. A schedule
-- is the difference between a tool and an operating system: research
-- ready before he is awake, checks that ran overnight.
--
-- Two things in here are safety rather than feature. `max_per_day` and
-- the month-spend guard in app/scheduler.py exist because this is the
-- first code that spends the owner's money with nobody watching, and a
-- schedule that loops is a bill nobody notices until the month ends.

CREATE TABLE IF NOT EXISTS schedules (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    objective       TEXT NOT NULL,
    -- Local wall-clock, because "seven in the morning" is what the owner
    -- means and UTC is what the server thinks in. next_due_at carries the
    -- resolved instant so the query that finds due work stays trivial.
    hour            SMALLINT NOT NULL CHECK (hour BETWEEN 0 AND 23),
    minute          SMALLINT NOT NULL DEFAULT 0 CHECK (minute BETWEEN 0 AND 59),
    -- ISO weekdays, 1=Monday .. 7=Sunday. Empty means every day.
    days_of_week    SMALLINT[] NOT NULL DEFAULT '{}',
    timezone        TEXT NOT NULL DEFAULT 'Asia/Kolkata',

    enabled         BOOLEAN NOT NULL DEFAULT true,
    max_per_day     SMALLINT NOT NULL DEFAULT 2 CHECK (max_per_day BETWEEN 1 AND 24),

    next_due_at     TIMESTAMPTZ NOT NULL,
    last_run_at     TIMESTAMPTZ,
    last_workflow_id UUID REFERENCES workflows(id) ON DELETE SET NULL,
    last_outcome    TEXT,
    runs_today      SMALLINT NOT NULL DEFAULT 0,
    runs_today_on   DATE,

    created_by      TEXT NOT NULL DEFAULT 'owner',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_schedules_due
    ON schedules (next_due_at) WHERE enabled;

-- What the owner has already been told about. Without this the arrival
-- briefing either repeats itself every message or has to guess, and a
-- briefing that repeats is one you stop reading.
CREATE TABLE IF NOT EXISTS briefing_marks (
    id          BOOLEAN PRIMARY KEY DEFAULT true CHECK (id),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO briefing_marks (id) VALUES (true) ON CONFLICT DO NOTHING;
