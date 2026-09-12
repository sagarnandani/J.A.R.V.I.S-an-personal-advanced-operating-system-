-- A brief, turned into a change somebody can read before it is real.
--
-- JARVIS runs on the owner's own machine now and can write code. The
-- dangerous version of that is a process editing the files it is running
-- from. This table is the other version: every change is a branch, a
-- diff, and a test result, and it becomes real only when the owner says
-- so -- which is the same rule everything else here runs on.
--
-- The states, and why each exists:
--   planned    -- read and understood, nothing written
--   building   -- a worktree exists and files are being written
--   proposed   -- a branch exists with a diff and a test result
--   failed     -- it could not produce something that builds
--   approved   -- the owner accepted it; merging is still his to do
--   discarded  -- the owner said no; the branch is left for him to delete
CREATE TABLE IF NOT EXISTS change_requests (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Where the brief came from, when it came from a file.
    attachment_id UUID REFERENCES attachments(id) ON DELETE SET NULL,
    workflow_id   UUID REFERENCES workflows(id) ON DELETE SET NULL,

    title         TEXT NOT NULL,
    brief         TEXT NOT NULL,
    state         TEXT NOT NULL DEFAULT 'planned',
    reason        TEXT,

    -- What it said it would do, before it did anything. Kept separately
    -- from the diff so the two can be compared: a change that does more
    -- than its plan said is the one worth looking at hardest.
    plan          JSONB,

    branch        TEXT,
    diff          TEXT,
    files_changed INTEGER NOT NULL DEFAULT 0,
    -- Whether the repository's own tests passed inside the worktree, and
    -- what they said. A proposal with no test result is not a proposal.
    tests_passed  BOOLEAN,
    tests_output  TEXT,

    spend_inr     NUMERIC(12,4) NOT NULL DEFAULT 0,
    shadow_inr    NUMERIC(12,4) NOT NULL DEFAULT 0,

    decided_by    TEXT,
    decided_at    TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_change_requests_new
    ON change_requests (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_change_requests_state
    ON change_requests (state, created_at DESC);
