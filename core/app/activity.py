"""What is happening right now, as one answer both the screen and the
conversation can use.

This exists because the same question kept being unanswerable. The owner
asked for a script, was told the Media Director was working on it, and
there was no script. Neither of us could tell whether nothing had ever
started, or something had started and stalled -- so the fixes were
guesses, and each one addressed a different half of a thing we could not
see.

One query answers it. Anything running, anything queued, anything being
made and how far it got, and how long each has been that way. Where the
answer is nothing, it says nothing in words rather than returning an
empty list that reads as a gap.

The conversation gets the same figures the panel does. A model that is
handed the true state has nothing to fill in, and one that says something
untrue is contradicted by the line directly beneath it.
"""
import logging
from datetime import datetime, timezone

from app.db import fetch, fetchrow

logger = logging.getLogger("jarvis.activity")

# Longer than any step legitimately takes. Past this, something is stuck
# rather than slow, and saying so is more useful than saying "working".
STALLED_MINUTES = 5


def _ago(when) -> tuple[int | None, str]:
    if when is None:
        return None, "at an unknown time"
    try:
        minutes = int((datetime.now(timezone.utc) - when).total_seconds() // 60)
    except (TypeError, ValueError):
        return None, "at an unknown time"
    if minutes < 1:
        return minutes, "just now"
    if minutes < 60:
        return minutes, f"{minutes} min ago"
    return minutes, f"{minutes // 60}h {minutes % 60}m ago"


async def snapshot() -> dict:
    """Everything in flight, and an honest word for it."""
    tasks = [dict(r) for r in await fetch(
        """
        SELECT t.id, t.capability, t.objective, t.status, t.started_at,
               t.created_at, t.attempts, t.workflow_id,
               a.name AS agent, w.objective AS workflow
          FROM tasks t
          LEFT JOIN agents a ON a.capability = t.capability
                            AND a.status = 'active'
          LEFT JOIN workflows w ON w.id = t.workflow_id
         WHERE t.status IN ('running', 'queued', 'blocked', 'waiting_approval')
           AND (w.id IS NULL OR w.status NOT IN ('completed','failed','cancelled'))
         ORDER BY t.created_at
         LIMIT 40
        """
    )]

    pieces = [dict(r) for r in await fetch(
        """
        SELECT c.id, c.topic, c.brand, c.created_at, c.workflow_id,
               count(t.id) FILTER (WHERE t.status = 'completed') AS done,
               count(t.id)                                       AS steps,
               max(t.capability) FILTER (WHERE t.status = 'running') AS at_step
          FROM content_pieces c
          LEFT JOIN tasks t ON t.workflow_id = c.workflow_id
         WHERE c.state = 'producing'
         GROUP BY c.id
         ORDER BY c.created_at DESC
         LIMIT 10
        """
    )]

    for row in tasks:
        row["id"] = str(row["id"])
        row["workflow_id"] = str(row["workflow_id"]) if row["workflow_id"] else None
        minutes, said = _ago(row["started_at"] or row["created_at"])
        row["minutes"] = minutes
        row["since"] = said
        row["stalled"] = bool(minutes is not None and minutes >= STALLED_MINUTES)

    for row in pieces:
        row["id"] = str(row["id"])
        row["workflow_id"] = str(row["workflow_id"]) if row["workflow_id"] else None
        minutes, said = _ago(row["created_at"])
        row["minutes"] = minutes
        row["since"] = said
        row["stalled"] = bool(minutes is not None and minutes >= STALLED_MINUTES)

    running = [t for t in tasks if t["status"] == "running"]
    waiting = [t for t in tasks if t["status"] == "waiting_approval"]
    stalled = [t for t in tasks if t["stalled"] and t["status"] == "running"]

    # Ever, not just now. "Nothing is running" and "nothing has ever been
    # started" are different answers to "where is my script", and only the
    # second one says the request never got through.
    totals = await fetchrow(
        "SELECT count(*) AS pieces, "
        "count(*) FILTER (WHERE state = 'producing') AS producing "
        "FROM content_pieces"
    )

    return {
        "busy": bool(tasks or pieces),
        "running": running,
        "queued": [t for t in tasks if t["status"] in ("queued", "blocked")],
        "waiting_approval": waiting,
        "pieces": pieces,
        "stalled": bool(stalled or any(p["stalled"] for p in pieces)),
        "pieces_ever": int((totals or {}).get("pieces") or 0),
        "said": _in_words(tasks, pieces, stalled, int((totals or {}).get("pieces") or 0)),
    }


def _in_words(tasks, pieces, stalled, pieces_ever) -> str:
    """One sentence of truth, for the panel and for the system prompt.

    Written here rather than in either caller so the screen and the
    conversation cannot disagree about what is happening -- which is
    exactly what went wrong.
    """
    if not tasks and not pieces:
        if pieces_ever == 0:
            return ("Nothing is running, and no piece of content has ever "
                    "been started on this deployment.")
        return "Nothing is running. No work is in progress at all."

    parts = []
    if pieces:
        made = "; ".join(
            f"{p['topic']} ({p['done']}/{p['steps']} steps, started {p['since']})"
            for p in pieces[:3]
        )
        parts.append(f"{len(pieces)} piece(s) being made: {made}")
    live = [t for t in tasks if t["status"] == "running"]
    if live:
        parts.append(
            f"{len(live)} step(s) running: "
            + "; ".join(f"{t['capability']} ({t['since']})" for t in live[:3])
        )
    queued = [t for t in tasks if t["status"] in ("queued", "blocked")]
    if queued:
        parts.append(f"{len(queued)} step(s) waiting to run")

    said = ". ".join(parts) + "."
    if stalled:
        said += (" This has been going far longer than a run should take, so "
                 "it is stuck rather than slow. Say that plainly.")
    return said
