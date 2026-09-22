-- Trying a new version of an agent against the one already doing the job.
--
-- The registry has kept version history since the agent foundation, and
-- `set_status` will stand one version down and another up. What has
-- never existed is the thing in between: running both for a while and
-- deciding from what actually happened. Without it, "promotion" means
-- somebody's opinion that the new one looks better, which is exactly the
-- judgement this system is supposed to replace with a measurement.
CREATE TABLE IF NOT EXISTS agent_trials (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    capability        TEXT NOT NULL,
    candidate_version INT  NOT NULL,
    baseline_version  INT  NOT NULL,

    -- The fraction of this capability's tasks the candidate takes.
    -- Capped at half: a trial that sends most of the work to an unproven
    -- version is not a trial, it is a deployment with a hopeful name.
    share             NUMERIC(3,2) NOT NULL DEFAULT 0.20
                      CHECK (share > 0 AND share <= 0.5),

    status            TEXT NOT NULL DEFAULT 'running'
                      CHECK (status IN ('running','promoted','rejected','abandoned')),
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at          TIMESTAMPTZ,
    started_by        TEXT NOT NULL,
    decided_by        TEXT,

    -- The numbers the decision was made on, kept with the decision. A
    -- promotion whose record does not say what it was promoted on gives
    -- a later rollback nothing to reason about.
    verdict           JSONB NOT NULL DEFAULT '{}'::jsonb,

    CHECK (candidate_version <> baseline_version)
);

-- One trial at a time per capability. Two candidates running at once
-- makes "which change caused this" unanswerable, which is the only
-- question a trial exists to answer.
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_running_trial_per_capability
    ON agent_trials (capability) WHERE status = 'running';

CREATE INDEX IF NOT EXISTS idx_agent_trials_recent
    ON agent_trials (capability, started_at DESC);
