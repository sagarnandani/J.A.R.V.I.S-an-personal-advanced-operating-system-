-- What the owner hands JARVIS to read.
--
-- Typing a long brief into a chat box on an iPad was the worst part of
-- using this. Now a file is attached, read once, and stays on record --
-- so "what did that document say" has an answer tomorrow without
-- attaching it again.
--
-- The TEXT is kept and the original bytes are not. Everything the owner
-- attaches is a document to read: a brief, a transcript, a page of
-- notes. Keeping the bytes would mean a volume to manage, a backup story
-- and a deletion story, for a copy of a file he already has.
CREATE TABLE IF NOT EXISTS attachments (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    filename     TEXT NOT NULL,
    media_type   TEXT NOT NULL DEFAULT '',
    -- Of the original upload, so the size shown matches the file on his
    -- device rather than the text pulled out of it.
    bytes        INTEGER NOT NULL DEFAULT 0,
    -- What was actually read. NOT NULL: a file nothing could be read from
    -- is refused at the door rather than stored as an empty row that
    -- looks like it worked.
    content      TEXT NOT NULL,
    chars        INTEGER NOT NULL DEFAULT 0,
    pages        INTEGER,
    -- Same file attached twice is the same row. Re-reading a hundred-page
    -- brief because it was sent again would cost money for nothing.
    sha256       TEXT NOT NULL,
    uploaded_by  TEXT NOT NULL,
    -- What came of it, filled in as things happen: the message that read
    -- it, any work it led to. Null means it was read and nothing followed,
    -- which is a normal outcome and not a failure.
    outcome      JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_attachments_same ON attachments (sha256);
CREATE INDEX IF NOT EXISTS idx_attachments_new ON attachments (created_at DESC);
