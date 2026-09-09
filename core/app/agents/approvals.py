"""Saying yes, and what happens next.

The foundation could always stop and ask. It could not be answered. A
task that needed approval moved to 'waiting_approval', the workflow
reported it, and there it stayed -- for ever, because nothing could
record a decision and nothing could start the work again.

Every publishing gate in the Media Company rests on this, so it is the
first thing built.

Three things it is careful about.

**A decision is recorded, not just acted on.** Who decided, when, and
what they were looking at. Staged autonomy -- "this workflow has passed
twenty times unedited, let it publish itself" -- is a query over these
rows, and an approval history that does not say what was approved is not
evidence of anything.

**Resuming goes back through the same door.** The task is returned to the
queue and run by `runtime.run_task` exactly as it would have been the
first time, with every permission, budget and cost check intact. A
resume path that skipped them would mean the one thing an owner
explicitly approved is the one thing nobody checked.

**A decision cannot be quietly reversed.** One row per task per category,
enforced by the database. Approving twice is not twice as approved, and
without the constraint a rejection could be overwritten by a later yes.
"""
import logging
from typing import Any
from uuid import UUID

from app.db import execute, fetch, fetchrow

logger = logging.getLogger("jarvis.approvals")


async def decide(
    task_id: UUID, category: str, decision: str, decided_by: str,
    reason: str | None = None, saw: dict[str, Any] | None = None,
) -> dict | None:
    """Record the owner's answer. Returns the row, or None if refused.

    Refuses to overwrite an existing decision. The database enforces it
    too; this is the half that can say why.
    """
    if decision not in {"approved", "rejected"}:
        return None

    task = await fetchrow(
        "SELECT id, workflow_id, status FROM tasks WHERE id = $1", task_id
    )
    if task is None:
        return None

    row = await fetchrow(
        """
        INSERT INTO task_approvals (task_id, workflow_id, category, decision,
                                    decided_by, reason, saw)
        VALUES ($1,$2,$3,$4,$5,$6,$7)
        ON CONFLICT (task_id, category) DO NOTHING
        RETURNING *
        """,
        task_id, task["workflow_id"], category, decision, decided_by,
        reason, saw or {},
    )
    if row is None:
        logger.info("Task %s already has a decision for '%s'.", task_id, category)
        return None
    return dict(row)


async def granted(task_id: UUID | None, category: str) -> bool:
    """Has this exact task already been approved for this exact thing?

    Per task, not per category in general. An owner approving one post is
    not approving publishing, and the difference is the whole point of
    asking.
    """
    if task_id is None:
        return False
    row = await fetchrow(
        "SELECT decision FROM task_approvals WHERE task_id = $1 AND category = $2",
        task_id, category,
    )
    return bool(row) and row["decision"] == "approved"


async def waiting() -> list[dict]:
    """Everything sitting on the owner's desk, with what it is waiting for."""
    rows = await fetch(
        """
        SELECT t.id, t.objective, t.capability, t.failure_reason AS asking_for,
               t.workflow_id, t.updated_at, t.result, t.spend_inr, t.shadow_inr,
               w.objective AS workflow_objective
          FROM tasks t
          LEFT JOIN workflows w ON w.id = t.workflow_id
         WHERE t.status = 'waiting_approval'
         ORDER BY t.updated_at DESC
        """
    )
    return [dict(r) for r in rows]


async def resume(task_id: UUID) -> bool:
    """Put an approved task back in the queue.

    Queued, not run directly. It goes through `runtime.run_task` like
    anything else, so the permission check it stopped at runs again -- and
    passes this time, because the decision is now on record. The one
    action the owner explicitly approved must not be the one action that
    skips the checks.
    """
    result = await execute(
        "UPDATE tasks SET status = 'queued', failure_reason = NULL, "
        "updated_at = now() WHERE id = $1 AND status = 'waiting_approval'",
        task_id,
    )
    return result.endswith("1")


async def abandon(task_id: UUID, reason: str) -> bool:
    """Stop a rejected task, and everything waiting on it.

    Cancelled rather than failed: nothing went wrong, the owner said no.
    The distinction matters when the metrics are read later -- a rejection
    rate is a signal about the work, a failure rate is a signal about the
    system.
    """
    result = await execute(
        "UPDATE tasks SET status = 'cancelled', failure_reason = $2, "
        "finished_at = now(), updated_at = now() "
        "WHERE id = $1 AND status = 'waiting_approval'",
        task_id, reason[:500],
    )
    if not result.endswith("1"):
        return False

    # Anything downstream of it can never run now, so it is closed too
    # rather than left blocked for ever with no explanation.
    await execute(
        """
        UPDATE tasks SET status = 'cancelled',
                         failure_reason = 'A step it depended on was rejected.',
                         finished_at = now(), updated_at = now()
         WHERE $1 = ANY(depends_on)
           AND status NOT IN ('completed', 'failed', 'cancelled')
        """,
        task_id,
    )
    return True
