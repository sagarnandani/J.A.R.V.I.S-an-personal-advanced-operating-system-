"""One row per piece: what exists, what it cost, and what the owner said.

Separate from the workflow on purpose. A workflow records what happened
-- five tasks, their costs, their results -- and it is the right place
for that. But "show me what we made this week" read out of task rows
means knowing which capability wrote the script, which review was the
last one, and how the director settled it, every time anybody asks.

So the piece is written down once, when production starts, and updated
when it settles. Nothing here computes anything: every number comes from
the rows that produced it.
"""
import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.db import execute, fetch, fetchrow

logger = logging.getLogger("jarvis.media.records")

# States the owner can still act on. Anything else is history.
OPEN = ("producing", "ready", "needs_you")

# What a decision leaves behind.
DECISIONS = {"approve": "approved", "discard": "discarded"}


async def open_piece(workflow_id: UUID, topic: str, brand: str) -> UUID | None:
    """Record that a piece is being made, before it is made.

    Written up front so a run that dies mid-way leaves evidence rather
    than nothing. A piece stuck at 'producing' is a visible problem; a
    piece that was never recorded is an invisible one.
    """
    row = await fetchrow(
        """
        INSERT INTO content_pieces (workflow_id, topic, brand)
        VALUES ($1, $2, $3)
        ON CONFLICT (workflow_id) DO UPDATE SET updated_at = now()
        RETURNING id
        """,
        workflow_id, topic, brand,
    )
    return row["id"] if row else None


async def settle(
    workflow_id: UUID,
    *,
    state: str,
    reason: str = "",
    package: Any = None,
    review: Any = None,
    spend_inr: Decimal | float = 0,
    shadow_inr: Decimal | float = 0,
    title: str = "",
) -> None:
    """Write down how it ended.

    COALESCE on package and review so a later update -- an owner's
    decision, say -- never blanks the work by not mentioning it.
    """
    await execute(
        """
        UPDATE content_pieces
           SET state = $2, reason = $3,
               package = COALESCE($4, package),
               review = COALESCE($5, review),
               title = COALESCE(NULLIF($6, ''), title),
               spend_inr = $7, shadow_inr = $8,
               updated_at = now()
         WHERE workflow_id = $1
        """,
        workflow_id, state, reason or None, package, review, title,
        Decimal(str(spend_inr)), Decimal(str(shadow_inr)),
    )


async def decide(piece_id: UUID, decision: str, by: str) -> dict | None:
    """The owner's yes or no. Returns the piece, or None if it is not open.

    Guarded on the current state rather than checked-then-written: two
    taps on a slow connection are two requests, and the second must not
    quietly overwrite the first.
    """
    state = DECISIONS.get(decision)
    if state is None:
        return None
    row = await fetchrow(
        """
        UPDATE content_pieces
           SET state = $2, decided_by = $3, decided_at = now(),
               updated_at = now()
         WHERE id = $1 AND state IN ('ready', 'needs_you')
        RETURNING *
        """,
        piece_id, state, by,
    )
    return dict(row) if row else None


async def get(piece_id: UUID) -> dict | None:
    row = await fetchrow("SELECT * FROM content_pieces WHERE id = $1", piece_id)
    return dict(row) if row else None


async def recent(limit: int = 20, state: str | None = None) -> list[dict]:
    """Newest first, optionally one state only."""
    limit = min(max(int(limit), 1), 100)
    if state:
        rows = await fetch(
            "SELECT * FROM content_pieces WHERE state = $1 "
            "ORDER BY created_at DESC LIMIT $2",
            state, limit,
        )
    else:
        rows = await fetch(
            "SELECT * FROM content_pieces ORDER BY created_at DESC LIMIT $1",
            limit,
        )
    return [dict(r) for r in rows]


async def waiting() -> list[dict]:
    """Pieces the owner still has to look at."""
    rows = await fetch(
        "SELECT * FROM content_pieces WHERE state IN ('ready', 'needs_you') "
        "ORDER BY created_at DESC LIMIT 20"
    )
    return [dict(r) for r in rows]


async def in_progress() -> list[dict]:
    """Pieces being made right now, and which step each has reached.

    JARVIS was asked why a script was taking so long and invented an
    answer -- "some topics take longer to research" -- because it had no
    idea whether anything was running at all. Its status notes carried
    finished work and work waiting on the owner, and nothing about work in
    flight, so the one question it could not answer was the one being
    asked.
    """
    rows = await fetch(
        """
        SELECT c.id, c.topic, c.brand, c.created_at,
               count(t.id) FILTER (WHERE t.status = 'completed')  AS done,
               count(t.id)                                        AS steps,
               max(t.capability) FILTER (WHERE t.status = 'running') AS running,
               bool_or(t.status = 'failed')                       AS broke
          FROM content_pieces c
          LEFT JOIN tasks t ON t.workflow_id = c.workflow_id
         WHERE c.state = 'producing'
         GROUP BY c.id
         ORDER BY c.created_at DESC
         LIMIT 10
        """
    )
    return [dict(r) for r in rows]


async def economics(days: int = 30) -> dict:
    """What the last month of making things actually cost.

    Both figures reported side by side and never added. On the free tier
    the real one is zero and true, and the shadow one is the only thing
    that makes two pieces comparable -- so a summary that showed one
    without the other would be either useless or misleading.
    """
    row = await fetchrow(
        """
        SELECT COUNT(*)                                            AS pieces,
               COUNT(*) FILTER (WHERE state = 'approved')          AS approved,
               COUNT(*) FILTER (WHERE state IN ('declined','stopped','rejected'))
                                                                   AS not_made,
               COALESCE(SUM(spend_inr), 0)                         AS spend_inr,
               COALESCE(SUM(shadow_inr), 0)                        AS shadow_inr
          FROM content_pieces
         WHERE created_at > now() - ($1 || ' days')::interval
        """,
        str(int(days)),
    )
    data = dict(row) if row else {}
    made = int(data.get("approved") or 0)
    return {
        "days": int(days),
        "pieces": int(data.get("pieces") or 0),
        "approved": made,
        "not_made": int(data.get("not_made") or 0),
        "spend_inr": float(data.get("spend_inr") or 0),
        "shadow_inr": float(data.get("shadow_inr") or 0),
        # Per approved piece, because a piece that was correctly not made
        # still cost something to decide against, and hiding that would
        # make declining look free.
        "shadow_per_approved": (
            round(float(data.get("shadow_inr") or 0) / made, 2) if made else None
        ),
    }
