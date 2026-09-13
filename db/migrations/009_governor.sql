-- What JARVIS may approve for itself, and what it decided about each change.
--
-- Until now every proposed change was the same as every other one: a
-- diff waiting for the owner. That is safe and it does not scale -- a
-- typo in a documentation file and a rewrite of the orchestrator arrive
-- looking identical, so either everything is read carefully or nothing
-- is.
--
-- Two things are added here.
--
-- `governor_policy` is a single row holding one number: the highest risk
-- level JARVIS may approve without asking. It starts at 0, meaning it
-- may approve nothing. Raising it is the owner's deliberate act and is
-- recorded with who did it. The CHECK is what makes level 4 impossible
-- rather than merely discouraged: the protected core cannot be reached
-- through this setting, however the code above it changes.
--
-- The rest hangs off change_requests, so that a change carries its own
-- risk assessment, the auditor's findings, and the decision -- and the
-- three can be read together afterwards. An approval with no record of
-- what was known at the time is not evidence of anything.

CREATE TABLE IF NOT EXISTS governor_policy (
    -- One row, for ever. The CHECK on a boolean primary key is the
    -- ordinary way to say that in Postgres.
    id                   BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
    -- 0 = ask about everything. 3 = approve up to high-risk alone.
    -- 4 is absent on purpose and the constraint enforces it: the
    -- protected core is never reachable by raising a setting.
    max_autonomous_level INTEGER NOT NULL DEFAULT 0
                         CHECK (max_autonomous_level BETWEEN 0 AND 3),
    updated_by           TEXT,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO governor_policy (id, max_autonomous_level, updated_by)
VALUES (TRUE, 0, 'default')
ON CONFLICT (id) DO NOTHING;

-- What the change was judged to be, by whom, on what evidence.
ALTER TABLE change_requests
    ADD COLUMN IF NOT EXISTS risk_level    INTEGER,
    ADD COLUMN IF NOT EXISTS risk          JSONB,
    ADD COLUMN IF NOT EXISTS audit         JSONB,
    ADD COLUMN IF NOT EXISTS governor      JSONB,
    -- Set when the Governor approved it without asking, so that
    -- "JARVIS decided this" and "you decided this" are never confused
    -- when the history is read back.
    ADD COLUMN IF NOT EXISTS autonomous    BOOLEAN NOT NULL DEFAULT FALSE,
    -- The commit the working tree was on when this change was built.
    -- What to go back to if it turns out to be wrong.
    ADD COLUMN IF NOT EXISTS based_on      TEXT,
    ADD COLUMN IF NOT EXISTS reverted_at   TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS revert_branch TEXT;

CREATE INDEX IF NOT EXISTS idx_change_requests_risk
    ON change_requests (risk_level, created_at DESC);

-- The last version of JARVIS known to start and pass its own health
-- check. Section 17 of the brief: self-development must never be capable
-- of destroying the only working version.
--
-- Recorded by the running application at startup, once it is actually
-- serving. Nothing in JARVIS writes to this except that path, and
-- nothing in JARVIS can check out a commit -- recovery is a command the
-- owner runs, and this table is what tells him which commit to name.
CREATE TABLE IF NOT EXISTS known_good (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    commit_sha  TEXT NOT NULL,
    branch      TEXT,
    noted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- What was true when it was recorded, so a bad entry can be
    -- recognised later rather than trusted because it is in the table.
    healthy     BOOLEAN NOT NULL DEFAULT TRUE,
    detail      JSONB
);

CREATE INDEX IF NOT EXISTS idx_known_good_recent
    ON known_good (noted_at DESC);
