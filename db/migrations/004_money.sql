-- Money in and money out.
--
-- The owner asked for this on day one: walk in and be told what was done,
-- what was earned, what was spent. Two of those were measurable; income
-- was not, and the briefing said so in those words rather than leaving a
-- gap for a model to fill with something plausible.
--
-- Recorded from what the owner says, because that is how they use JARVIS.
-- A form to fill in is a form that does not get filled in. Bank or
-- invoice imports can land in this same table later; `source` is here so
-- a row's origin is never in doubt once they do.

CREATE TABLE IF NOT EXISTS money_events (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- 'in' is money received, 'out' is money spent. Deliberately not a
    -- signed amount: a sign is easy to lose in a sum and impossible to
    -- see in a row.
    direction    TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    amount_inr   NUMERIC(14,2) NOT NULL CHECK (amount_inr > 0),
    what         TEXT NOT NULL,
    category     TEXT,
    occurred_on  DATE NOT NULL DEFAULT CURRENT_DATE,

    -- Where the figure came from. 'stated' is the owner saying it in so
    -- many words; anything else is a later import. Never a guess: a row
    -- exists only when a real amount was given.
    source       TEXT NOT NULL DEFAULT 'stated',
    -- The exact words this was read out of, so the chain from a number
    -- back to what was actually said is never broken.
    said_in      UUID REFERENCES memories(id) ON DELETE SET NULL,

    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_money_when
    ON money_events (occurred_on DESC, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_money_direction
    ON money_events (direction, occurred_on DESC);
