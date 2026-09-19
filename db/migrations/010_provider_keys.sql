-- Model provider keys, set from the dashboard instead of a text editor.
--
-- Until now a key meant an SSH session, an editor, and a container
-- rebuild. That is four things to get right on a laptop, and the owner
-- reads this system on an iPad. The old Render deployment asked for keys
-- in a web form and never put them in git -- this is that, brought
-- in-house.
--
-- WHAT THIS IS NOT. The value is stored as written. It is not encrypted,
-- because encrypting it needs a key, which needs somewhere to live,
-- which is the same problem one layer down. So the honest statement is:
-- these are as protected as the database is. On a single-user machine
-- behind a front door that is the same protection an .env file has, and
-- saying otherwise would be worse than saying this.
--
-- What it is NOT stored in: the repository. That is the property worth
-- having. A key in git is a key in every clone, every fork and every
-- backup, for ever, and no amount of deleting it later takes it back.
CREATE TABLE IF NOT EXISTS provider_keys (
    provider    TEXT PRIMARY KEY
                CHECK (provider IN ('gemini', 'openai', 'claude')),
    api_key     TEXT NOT NULL CHECK (length(api_key) > 0),
    -- The model to use with it, when the default is not wanted. Kept
    -- beside the key so changing provider is one action, not two.
    model       TEXT,
    set_by      TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
