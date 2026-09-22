-- Which model actually produced each result.
--
-- agent_metrics has recorded success, failure, latency, cost and
-- confidence since the agent foundation. What it has never recorded is
-- WHO produced them -- the model is in the telemetry detail blob, which
-- is written for reading by a person, not for grouping by.
--
-- So "which model is best at coding" was unanswerable from the data
-- JARVIS was already collecting, and section 15 of the brief -- the
-- router learning from measured performance -- had nothing to learn
-- from. Two columns fix that.
--
-- Nullable, because every row written before this migration genuinely
-- does not know, and inventing a value would poison the first thing
-- that reads it.
ALTER TABLE agent_metrics
    ADD COLUMN IF NOT EXISTS provider TEXT,
    ADD COLUMN IF NOT EXISTS model    TEXT,
    -- The tier that was asked for, which is not always the tier that
    -- answered: the router drops one when the budget is short. Recorded
    -- separately so "deep costs more and does better" can be checked
    -- rather than assumed.
    ADD COLUMN IF NOT EXISTS tier     TEXT;

-- The query the router makes on every routed task: how has this
-- capability fared, per model, recently. Without the index that is a
-- sequential scan of every measurement ever taken.
CREATE INDEX IF NOT EXISTS idx_agent_metrics_by_model
    ON agent_metrics (capability, model, metric, created_at DESC);
