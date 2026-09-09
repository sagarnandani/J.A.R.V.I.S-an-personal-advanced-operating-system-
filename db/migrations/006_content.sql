-- The record of what the Media Company actually made.
--
-- A workflow already records the work: five task rows, their costs, their
-- results. What it does not record is the piece -- the thing the owner
-- looks at, decides on, and later wants to find again. Reading a piece
-- back out of five task rows means knowing which capability produced the
-- script, which review was the last one, and how the director settled it.
-- That is a query nobody should have to write twice, and it is the query
-- the Media tab, the arrival briefing and the economics all need.
--
-- So one row per piece, written when production starts and updated when
-- it settles. The workflow stays the source of truth for what happened;
-- this is the source of truth for what exists.
CREATE TABLE IF NOT EXISTS content_pieces (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id  UUID UNIQUE REFERENCES workflows(id) ON DELETE CASCADE,
    brand        TEXT NOT NULL,
    topic        TEXT NOT NULL,

    -- Where it got to. Not the workflow's status: a workflow that
    -- completed cleanly may have completed by deciding not to publish,
    -- and those are different things to whoever reads this tomorrow.
    --   producing  -- running now
    --   stopped    -- verification contradicted a load-bearing claim
    --   declined   -- the strategist said it should not exist
    --   rejected   -- editorial rejected it
    --   needs_you  -- still not right after a revision
    --   ready      -- passed review, waiting for the owner
    --   approved   -- the owner said yes
    --   discarded  -- the owner said no
    --   failed     -- the machinery broke, which is none of the above
    state        TEXT NOT NULL DEFAULT 'producing',
    reason       TEXT,

    title        TEXT,
    -- The package as the script agent returned it, and the reviewer's
    -- notes as the reviewer returned them. Stored whole rather than
    -- flattened: the fields worth showing changed twice while this was
    -- being written, and a column per field would have meant a migration
    -- each time.
    package      JSONB,
    review       JSONB,

    -- Two numbers, never summed. spend_inr is money that was billed;
    -- shadow_inr is what the same work would have cost on a paid
    -- equivalent, which is the only figure that makes two pieces
    -- comparable while the real one is zero.
    spend_inr    NUMERIC(12,4) NOT NULL DEFAULT 0,
    shadow_inr   NUMERIC(12,4) NOT NULL DEFAULT 0,

    decided_by   TEXT,
    decided_at   TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_content_pieces_new
    ON content_pieces (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_content_pieces_state
    ON content_pieces (state, created_at DESC);

COMMENT ON COLUMN content_pieces.shadow_inr IS
    'Estimated economic cost on a paid equivalent. Never real money.';
