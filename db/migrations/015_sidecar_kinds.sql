-- What kind of thing a sidecar is, because it decides what it can do.
--
-- The first sidecar was a Python program on a Mac or a Linux box, and it
-- can do everything on the list. An iPad cannot run that: iOS does not
-- allow arbitrary background processes, and no amount of wanting it to
-- changes that.
--
-- What an iPad DOES have is the dashboard, already open in Safari. A web
-- page can do a real subset -- say something aloud, show a notification,
-- put a link in front of the owner -- and nothing else. It cannot run a
-- command, read a file, or take a screenshot without a picker.
--
-- So a sidecar has a kind, and the kind decides what it may be granted.
-- Without this, pairing an iPad with 'terminal' would succeed and then
-- every job queued for it would fail for ever, which is the worst shape
-- a limitation can take: invisible until it matters.
ALTER TABLE sidecars
    ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'native'
        CHECK (kind IN ('native', 'browser'));

ALTER TABLE sidecar_pairings
    ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'native'
        CHECK (kind IN ('native', 'browser'));
