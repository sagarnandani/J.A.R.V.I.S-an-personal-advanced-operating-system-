-- What a task is actually waiting for the owner to say yes to.
--
-- Until now a parked task carried only `failure_reason` -- the sentence
-- the owner reads -- and the approval category was worked out afterwards
-- by looking at the permissions the task had declared. That was fine
-- while every approval category came from a permission. It stops being
-- fine the moment something asks for a reason the permission set cannot
-- express: "this exact command", where the permission (run_command) is
-- held for both the commands that ask and the ones that do not.
--
-- Two columns, so the thing being approved is recorded as data rather
-- than recovered by parsing the prose that described it.
ALTER TABLE tasks
    -- The category the runtime actually stopped on, from the exception
    -- that stopped it, rather than inferred later from something else.
    ADD COLUMN IF NOT EXISTS awaiting_category TEXT,
    -- The specifics the owner is being shown: for a command, the exact
    -- argument vector. An approval history is evidence only if it
    -- records the thing that was approved, and "approved: running a
    -- command" is not evidence of anything.
    ADD COLUMN IF NOT EXISTS awaiting_detail JSONB;
