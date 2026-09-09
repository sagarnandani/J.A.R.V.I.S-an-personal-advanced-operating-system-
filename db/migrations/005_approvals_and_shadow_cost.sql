-- Two things the Media Company cannot work without.
--
-- 1. A record of approval DECISIONS.
--
-- `approvals` already exists and is a policy table: one row per category
-- saying whether that kind of action needs asking about. It has no idea
-- which task was approved, by whom, or what they were looking at when
-- they decided. So a task that needed approval waited at
-- 'waiting_approval' and stayed there for ever -- there was no way to say
-- yes.
--
-- This is also the substrate for staged autonomy. "This workflow has
-- passed twenty times unedited, let it publish itself" is a query over
-- these rows. Without them, Stage 2 has no evidence to stand on and
-- becomes a switch somebody flips on faith.
CREATE TABLE IF NOT EXISTS task_approvals (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id      UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    workflow_id  UUID REFERENCES workflows(id) ON DELETE SET NULL,
    category     TEXT NOT NULL,
    decision     TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
    decided_by   TEXT NOT NULL,
    reason       TEXT,
    -- What the owner was actually shown. An approval history is only
    -- evidence if it records the thing that was approved, not just that
    -- something was.
    saw          JSONB,
    decided_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One decision per category per task. Approving twice is not twice as
-- approved, and it would let a rejection be quietly overwritten.
CREATE UNIQUE INDEX IF NOT EXISTS idx_task_approvals_one
    ON task_approvals (task_id, category);
CREATE INDEX IF NOT EXISTS idx_task_approvals_when
    ON task_approvals (decided_at DESC);


-- 2. Shadow cost.
--
-- Gemini's free tier is priced at zero, honestly, because nothing is
-- billed. That makes every ratio in the content economics arithmetic on
-- nothing: cost Rs.0, therefore return infinite.
--
-- So each row carries two numbers. `spend_inr` is money that was actually
-- billed and stays the only thing reported as spend. `shadow_inr` is what
-- the same computation would reasonably have cost on a paid equivalent --
-- never money, never added to a bill, and useful precisely where the real
-- figure is zero: comparing workflows, comparing models, deciding which
-- formats are worth making.
ALTER TABLE tasks     ADD COLUMN IF NOT EXISTS shadow_inr NUMERIC(12,4) NOT NULL DEFAULT 0;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS shadow_cost NUMERIC(12,4);

COMMENT ON COLUMN tasks.shadow_inr IS
    'Estimated economic cost on a paid equivalent. Never real money.';
COMMENT ON COLUMN audit_log.shadow_cost IS
    'Estimated economic cost on a paid equivalent. Never real money.';
