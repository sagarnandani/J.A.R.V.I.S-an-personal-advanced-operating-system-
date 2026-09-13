"""One row per change JARVIS proposed, and what became of it."""
import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.db import execute, fetch, fetchrow

logger = logging.getLogger("jarvis.dev.records")

# States the owner can still act on. Anything else is history.
#
# 'refused' is the Governor's, not the owner's: a change that reached the
# protected core, or that the auditor stopped. It is kept rather than
# deleted because a refusal is the most interesting row in the table --
# it is the record of the boundary doing its job.
OPEN = ("planned", "building", "proposed")
DECISIONS = {"approve": "approved", "discard": "discarded"}


async def open_request(title: str, brief: str, attachment_id=None) -> dict:
    row = await fetchrow(
        "INSERT INTO change_requests (title, brief, attachment_id, state) "
        "VALUES ($1,$2,$3,'planned') RETURNING *",
        title[:200], brief, attachment_id,
    )
    return dict(row) if row else {}


async def update(request_id: UUID, **fields: Any) -> None:
    """Only the columns named. A change that fails half way should keep
    the plan it already produced rather than having it blanked."""
    allowed = {"state", "reason", "plan", "branch", "diff", "files_changed",
               "tests_passed", "tests_output", "workflow_id",
               "spend_inr", "shadow_inr", "title",
               "risk_level", "risk", "audit", "governor", "autonomous",
               "based_on", "reverted_at", "revert_branch",
               "decided_by", "decided_at"}
    # Dicts go straight to the jsonb columns: app/db.py registers a codec
    # for json and jsonb on every connection, so encoding them here would
    # store a JSON *string* containing JSON -- which reads back as a str
    # and breaks every caller that expects a dict.
    sets, values = [], []
    for i, (key, value) in enumerate((k, v) for k, v in fields.items()
                                     if k in allowed):
        sets.append(f"{key} = ${i + 2}")
        values.append(Decimal(str(value)) if key.endswith("_inr") else value)
    if not sets:
        return
    await execute(
        f"UPDATE change_requests SET {', '.join(sets)}, updated_at = now() "
        f"WHERE id = $1",
        request_id, *values,
    )


async def decide(request_id: UUID, decision: str, by: str) -> dict | None:
    """The owner's yes or no.

    Guarded on the current state rather than checked then written: two
    taps on a slow connection are two requests, and the second must not
    overwrite the first.

    Approving does not merge anything. The branch is his to merge, and
    that is deliberate -- a system that could merge its own changes is one
    tap away from a system that does.
    """
    state = DECISIONS.get(decision)
    if state is None:
        return None
    row = await fetchrow(
        "UPDATE change_requests SET state = $2, decided_by = $3, "
        "decided_at = now(), updated_at = now() "
        "WHERE id = $1 AND state = 'proposed' RETURNING *",
        request_id, state, by,
    )
    return dict(row) if row else None


async def get(request_id: UUID) -> dict | None:
    row = await fetchrow("SELECT * FROM change_requests WHERE id = $1", request_id)
    return dict(row) if row else None


async def recent(limit: int = 20) -> list[dict]:
    """Newest first, without the diffs.

    A listing carrying a hundred thousand characters of patch per row
    would be unusable on a phone, which is where this is read.
    """
    rows = await fetch(
        "SELECT id, title, state, reason, branch, files_changed, "
        "tests_passed, spend_inr, shadow_inr, created_at, decided_by, "
        "risk_level, autonomous, reverted_at "
        "FROM change_requests ORDER BY created_at DESC LIMIT $1",
        min(max(int(limit), 1), 100),
    )
    return [dict(r) for r in rows]


async def waiting() -> list[dict]:
    rows = await fetch(
        "SELECT id, title, branch, tests_passed FROM change_requests "
        "WHERE state = 'proposed' ORDER BY created_at DESC LIMIT 20"
    )
    return [dict(r) for r in rows]


async def refuse(request_id: UUID, why: str, governor: dict | None = None,
                 audit: dict | None = None, risk: dict | None = None) -> None:
    """The Governor said no. Recorded as its decision, not the owner's.

    Written even when the change was never built, so that "JARVIS tried
    to change the permission system and was stopped" leaves a row rather
    than a log line nobody reads.
    """
    from datetime import datetime, timezone

    await update(request_id, state="refused", reason=why,
                 governor=governor, audit=audit, risk=risk,
                 risk_level=(risk or {}).get("level"),
                 decided_by="governor", autonomous=False,
                 decided_at=datetime.now(timezone.utc))
    logger.warning("Governor refused change %s: %s", request_id, why)


async def approved_by_governor(request_id: UUID, why: str,
                               governor: dict, audit: dict,
                               risk: dict) -> None:
    """Approved without asking, within the ceiling the owner set.

    `autonomous` is what separates this from the owner's own approval in
    the history. Without that column, "you approved 40 changes" and
    "JARVIS approved 40 changes" are the same query.
    """
    from datetime import datetime, timezone

    # Stamped explicitly. `update` writes only the columns it is given,
    # so without this an autonomously approved change has no decision
    # time at all -- and "when did JARVIS approve this" is the first
    # question anyone asks of the history.
    await update(request_id, state="approved", reason=why,
                 governor=governor, audit=audit, risk=risk,
                 risk_level=risk.get("level"),
                 decided_by="governor", autonomous=True,
                 decided_at=datetime.now(timezone.utc))


async def autonomy_history(limit: int = 50) -> list[dict]:
    """What JARVIS decided for itself, so it can be read back as a whole."""
    rows = await fetch(
        "SELECT id, title, state, risk_level, autonomous, branch, reason, "
        "decided_by, decided_at, created_at FROM change_requests "
        "WHERE decided_by IS NOT NULL ORDER BY decided_at DESC NULLS LAST "
        "LIMIT $1",
        min(max(int(limit), 1), 200),
    )
    return [dict(r) for r in rows]
