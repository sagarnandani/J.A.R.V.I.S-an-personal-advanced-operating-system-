-- Eyes and hands on the owner's other machines.
--
-- The server is the brain and has no screen, no browser of the owner's,
-- no access to his Mac's files. A sidecar is a small program he runs on
-- one of those machines that offers a NAMED, LIMITED set of things it
-- will do on JARVIS's behalf.
--
-- Two decisions are worth stating here, because the schema enforces them.
--
-- The sidecar CONNECTS OUT and asks for work. JARVIS never dials into
-- his laptop. That is not a convenience: it means no port is opened on
-- his machine, nothing has to be forwarded through his router, it works
-- from a cafe, and the machine that decides whether to be reachable is
-- the machine itself. Closing the laptop lid is the off switch.
--
-- Capabilities are GRANTED PER SIDECAR by the owner, and a task can
-- never use more than the sidecar was granted. A Mac that offers
-- screenshots is not a Mac that offers a terminal, even if the same
-- program could do both.
CREATE TABLE IF NOT EXISTS sidecars (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name          TEXT NOT NULL,
    machine       TEXT NOT NULL DEFAULT '',

    -- What this one is allowed to do, chosen by the owner when he pairs
    -- it. Never written by the sidecar itself: a machine asking for more
    -- authority than it was given is the shape of the attack this whole
    -- table exists to prevent.
    capabilities  TEXT[] NOT NULL DEFAULT '{}',

    -- The token, hashed. Same rule as the provider keys: what is stored
    -- cannot be read back out and handed to anybody, including the owner
    -- -- he gets it once, at pairing.
    token_hash    TEXT NOT NULL,

    status        TEXT NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active', 'paused', 'revoked')),
    paired_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ,
    -- Whatever the sidecar says about itself: OS, version, hostname.
    -- Descriptive only. Nothing here may decide what it is permitted.
    reported      JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_sidecars_live
    ON sidecars (status, last_seen_at DESC);

-- Unique among the LIVE ones only. A revoked sidecar is history, and
-- history must not stop the owner pairing a replacement: he revokes
-- "Mac" because that laptop was stolen, buys another, and calls it
-- "Mac". A plain UNIQUE would refuse that, from the database, after the
-- pairing code had already been accepted.
CREATE UNIQUE INDEX IF NOT EXISTS idx_sidecars_one_live_name
    ON sidecars (name) WHERE status <> 'revoked';

-- One thing for one sidecar to do.
--
-- A queue rather than a call, because the sidecar is not reachable: it
-- asks for work when it can, does it, and brings the answer back. A
-- machine that is asleep has jobs waiting rather than jobs failing.
CREATE TABLE IF NOT EXISTS sidecar_jobs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sidecar_id    UUID NOT NULL REFERENCES sidecars(id) ON DELETE CASCADE,
    -- The task this was done for, so a screenshot has a reason attached
    -- to it and the audit trail joins up.
    task_id       UUID REFERENCES tasks(id) ON DELETE SET NULL,

    capability    TEXT NOT NULL,
    action        TEXT NOT NULL,
    arguments     JSONB NOT NULL DEFAULT '{}'::jsonb,

    status        TEXT NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued', 'sent', 'done', 'failed',
                                    'refused', 'expired', 'cancelled')),
    -- Set when the owner's approval is what is missing, so a job waiting
    -- on a person is distinguishable from a job waiting on a machine.
    needs_approval BOOLEAN NOT NULL DEFAULT FALSE,

    result        JSONB,
    error         TEXT,

    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at       TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ,
    -- A job nobody collected is not a job that runs next week. Anything
    -- older than this is expired rather than executed, because the
    -- context it was queued in is gone.
    expires_at    TIMESTAMPTZ NOT NULL DEFAULT now() + interval '10 minutes'
);

CREATE INDEX IF NOT EXISTS idx_sidecar_jobs_waiting
    ON sidecar_jobs (sidecar_id, status, created_at)
    WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS idx_sidecar_jobs_recent
    ON sidecar_jobs (created_at DESC);

-- A pairing code, good once and briefly.
--
-- The owner generates one in the dashboard and types it into the sidecar
-- on the other machine. Without this, anything that could reach the
-- server could claim to be a new pair of hands.
CREATE TABLE IF NOT EXISTS sidecar_pairings (
    code         TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    capabilities TEXT[] NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL DEFAULT now() + interval '15 minutes',
    used_at      TIMESTAMPTZ
);
