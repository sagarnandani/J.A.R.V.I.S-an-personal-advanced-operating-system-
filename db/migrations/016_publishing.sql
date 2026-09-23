-- Actually posting something, and the account it goes to.
--
-- The media chain researches, decides whether the piece should exist,
-- writes it, checks its facts, reviews it and stops at 'approved' --
-- the owner has said yes and nothing happens. Every gate was built and
-- the last step was missing, so the whole chain produced things nobody
-- could publish without copying and pasting.
--
-- Two decisions live in this schema.
--
-- The token is stored so it can be USED, which means it is readable by
-- anything that can read this table. That is unavoidable -- an API call
-- needs the real token, unlike a password, which only ever needs
-- comparing. So the mitigation is elsewhere: it is never logged, never
-- returned by any route, and the row says plainly what it can do.
--
-- A piece records WHERE it went and WHAT the platform called it. Without
-- the returned id, "did that post actually go out" is answerable only by
-- opening LinkedIn and looking -- which is exactly the kind of claim
-- this project refuses to make on trust.
CREATE TABLE IF NOT EXISTS social_accounts (
    platform      TEXT PRIMARY KEY CHECK (platform IN ('linkedin')),

    -- Who it posts AS, from the platform rather than from anything the
    -- owner typed. urn:li:person:xxxx.
    account_urn   TEXT NOT NULL,
    account_name  TEXT NOT NULL DEFAULT '',

    access_token  TEXT NOT NULL,
    refresh_token TEXT,
    -- LinkedIn's member tokens last 60 days and its refresh tokens are
    -- not granted to every app. Recorded so JARVIS can say "this needs
    -- reconnecting on the 4th" instead of discovering it mid-post.
    expires_at    TIMESTAMPTZ,
    scopes        TEXT[] NOT NULL DEFAULT '{}',

    connected_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    connected_by  TEXT NOT NULL DEFAULT '',
    last_post_at  TIMESTAMPTZ
);

-- Where a finished piece actually went.
ALTER TABLE content_pieces
    ADD COLUMN IF NOT EXISTS published_at  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS published_to  TEXT,
    -- The platform's own id for the post, and a link to it. Evidence,
    -- not a claim: section 33 of the brief -- never trust "I posted it".
    ADD COLUMN IF NOT EXISTS published_id  TEXT,
    ADD COLUMN IF NOT EXISTS published_url TEXT;

-- One short-lived secret per connection attempt, so a callback that
-- JARVIS did not start cannot hand it a token.
CREATE TABLE IF NOT EXISTS social_oauth_states (
    state      TEXT PRIMARY KEY,
    platform   TEXT NOT NULL,
    started_by TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT now() + interval '10 minutes',
    used_at    TIMESTAMPTZ
);
