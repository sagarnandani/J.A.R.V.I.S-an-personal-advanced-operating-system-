-- Agent Foundation: the machinery future agents plug into.
--
-- Design notes (plain language):
--
-- * Four new tables and one widened one. `agents` is the registry --
--   who exists and what they may do. `workflows` is one objective the
--   owner gave. `tasks` (already present since Stage 0, widened here)
--   is a single unit of delegated work. `agent_events` is the trace of
--   what actually happened, and `agent_metrics` is how well it went.
--
-- * `tasks` is WIDENED rather than replaced. Stage 0 created it with the
--   right idea and too few fields; a second task table would mean two
--   places to look for "what work is outstanding", which is exactly the
--   thing the brief says must never happen.
--
-- * Dependencies live in `tasks.depends_on` as an array of task ids
--   rather than in a join table. At a personal system's scale a graph
--   of tens of tasks is read whole, and one array is simpler to reason
--   about than an edge table. If workflows ever grow to thousands of
--   tasks this becomes an edge table; nothing above it would change.
--
-- * Everything money-related stays NUMERIC, never float. Rounding
--   errors in a budget are the kind of bug nobody notices until the
--   number is wrong by enough to matter.

-- ---------------------------------------------------------------- registry
CREATE TABLE IF NOT EXISTS agents (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- capability, not character: "research.web", not "Alfred".
    capability       TEXT NOT NULL,
    version          INTEGER NOT NULL DEFAULT 1,
    name             TEXT NOT NULL,
    description      TEXT NOT NULL DEFAULT '',
    domain           TEXT,
    supervisor       TEXT,
    -- What it can be asked to do, what it may touch, and how clever a
    -- model it is allowed to spend. Arrays and JSON because the shape of
    -- these will keep changing and a column per idea would not survive.
    task_types       TEXT[] NOT NULL DEFAULT '{}',
    tools            TEXT[] NOT NULL DEFAULT '{}',
    permissions      TEXT[] NOT NULL DEFAULT '{}',
    model_tiers      TEXT[] NOT NULL DEFAULT '{standard}',
    -- Lifecycle. An agent is never edited in place: a new version is
    -- registered and activated, so the old one can be compared against
    -- and rolled back to.
    status           TEXT NOT NULL DEFAULT 'experimental' CHECK (status IN (
                         'experimental', 'testing', 'active',
                         'degraded', 'disabled', 'retired'
                     )),
    max_cost_inr     NUMERIC(10,4),
    config           JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (capability, version)
);
CREATE INDEX IF NOT EXISTS idx_agents_capability ON agents (capability);
CREATE INDEX IF NOT EXISTS idx_agents_status ON agents (status);

-- --------------------------------------------------------------- workflows
CREATE TABLE IF NOT EXISTS workflows (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    objective      TEXT NOT NULL,
    requested_by   TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'planning' CHECK (status IN (
                       'planning', 'running', 'waiting_approval',
                       'completed', 'failed', 'cancelled'
                   )),
    budget_inr     NUMERIC(10,4),
    spend_inr      NUMERIC(12,6) NOT NULL DEFAULT 0,
    result         JSONB,
    failure_reason TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_workflows_status ON workflows (status);

-- ------------------------------------------------------------------- tasks
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS workflow_id     UUID REFERENCES workflows(id) ON DELETE CASCADE;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS parent_task_id  UUID REFERENCES tasks(id) ON DELETE CASCADE;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS capability      TEXT;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS agent_id        UUID REFERENCES agents(id);
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS priority        INTEGER NOT NULL DEFAULT 5;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS inputs          JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS expected_output TEXT;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS constraints     JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS depends_on      UUID[] NOT NULL DEFAULT '{}';
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS deadline        TIMESTAMPTZ;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS max_attempts    INTEGER NOT NULL DEFAULT 3;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS attempts        INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS result          JSONB;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS confidence      NUMERIC(3,2);
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS failure_reason  TEXT;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS spend_inr       NUMERIC(12,6) NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS started_at      TIMESTAMPTZ;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS finished_at     TIMESTAMPTZ;
-- Same objective submitted twice should not run twice. Nullable, because
-- most tasks are created by the planner and need no external key.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS idempotency_key TEXT;

-- Stage 0 allowed pending/in_progress/completed/failed/awaiting_approval.
-- A dependency graph needs "blocked" and the owner needs "cancelled".
ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_status_check;
ALTER TABLE tasks ADD CONSTRAINT tasks_status_check CHECK (status IN (
    'queued', 'blocked', 'running', 'waiting_approval',
    'completed', 'failed', 'cancelled',
    -- Stage 0 spellings, kept so existing rows stay valid.
    'pending', 'in_progress', 'awaiting_approval'
));

CREATE INDEX IF NOT EXISTS idx_tasks_workflow ON tasks (workflow_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks (status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_idempotency
    ON tasks (idempotency_key) WHERE idempotency_key IS NOT NULL;

-- ------------------------------------------------------------- observability
-- One row per notable thing that happened, so a finished workflow can be
-- replayed: why this agent, what context it got, which model, what it
-- cost, what came back, what failed, who approved.
CREATE TABLE IF NOT EXISTS agent_events (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id  UUID REFERENCES workflows(id) ON DELETE CASCADE,
    task_id      UUID REFERENCES tasks(id) ON DELETE CASCADE,
    capability   TEXT,
    kind         TEXT NOT NULL,
    detail       JSONB NOT NULL DEFAULT '{}'::jsonb,
    cost_inr     NUMERIC(12,6),
    duration_ms  INTEGER,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agent_events_workflow ON agent_events (workflow_id, created_at);
CREATE INDEX IF NOT EXISTS idx_agent_events_task ON agent_events (task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_agent_events_kind ON agent_events (kind, created_at DESC);

-- ------------------------------------------------------------- evaluation
-- Deliberately generic: metric name plus value. Domain-specific measures
-- (audience retention, say) are a row here later, not a schema change.
CREATE TABLE IF NOT EXISTS agent_metrics (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    capability   TEXT NOT NULL,
    agent_id     UUID REFERENCES agents(id) ON DELETE SET NULL,
    task_id      UUID REFERENCES tasks(id) ON DELETE CASCADE,
    metric       TEXT NOT NULL,
    value        NUMERIC(14,6) NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agent_metrics_lookup
    ON agent_metrics (capability, metric, created_at DESC);
