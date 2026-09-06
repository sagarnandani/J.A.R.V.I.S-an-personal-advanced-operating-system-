"""Every piece of delegated work, and its dependencies.

The one requirement that shapes this module: JARVIS must never lose track
of delegated work. So a task is a row before anything runs, its status
changes are writes, and nothing about its progress lives only in memory
where a restart would take it.

Dependencies are held on the task as an array of task ids. A task is
runnable when every task it names has completed. That is checked in SQL
rather than in Python, so two workers asking "what can run now?" cannot
both be told the same thing.
"""
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.agents.schemas import TaskStatus
from app.db import execute, fetch, fetchrow


async def create_workflow(
    objective: str, requested_by: str, budget_inr: Decimal | None = None
) -> UUID:
    row = await fetchrow(
        "INSERT INTO workflows (objective, requested_by, budget_inr) "
        "VALUES ($1,$2,$3) RETURNING id",
        objective, requested_by, budget_inr,
    )
    assert row is not None
    return row["id"]


async def create(
    *,
    objective: str,
    capability: str,
    workflow_id: UUID | None = None,
    parent_task_id: UUID | None = None,
    inputs: dict | None = None,
    expected_output: str = "",
    constraints: dict | None = None,
    depends_on: list[UUID] | None = None,
    priority: int = 5,
    budget_cost: Decimal | None = None,
    max_attempts: int = 3,
    idempotency_key: str | None = None,
    origin: str = "jarvis",
) -> UUID:
    """Record a task. Blocked if it names dependencies, queued otherwise.

    An idempotency key that has been seen before returns the original
    task rather than creating a second one -- the same objective
    submitted twice should not run twice, and "twice" is what a retried
    request or a double-tapped button looks like.
    """
    depends_on = depends_on or []
    status = TaskStatus.BLOCKED if depends_on else TaskStatus.QUEUED

    if idempotency_key:
        existing = await fetchrow(
            "SELECT id FROM tasks WHERE idempotency_key = $1", idempotency_key
        )
        if existing:
            return existing["id"]

    row = await fetchrow(
        """
        INSERT INTO tasks (objective, capability, workflow_id, parent_task_id,
                           inputs, expected_output, constraints, depends_on,
                           priority, budget_cost, max_attempts, status,
                           idempotency_key, origin)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
        RETURNING id
        """,
        objective, capability, workflow_id, parent_task_id,
        inputs or {}, expected_output,
        constraints or {}, depends_on,
        priority, budget_cost, max_attempts, status.value,
        idempotency_key, origin,
    )
    assert row is not None
    return row["id"]


async def get(task_id: UUID) -> dict | None:
    row = await fetchrow("SELECT * FROM tasks WHERE id = $1", task_id)
    return dict(row) if row else None


async def runnable(workflow_id: UUID, limit: int = 20) -> list[dict]:
    """Tasks whose dependencies have all completed.

    The NOT EXISTS is doing the real work: a task is runnable only when
    no task it depends on is in any state other than completed. Checking
    that in Python would mean reading the graph, deciding, and acting --
    with room in the middle for another worker to decide the same.
    """
    rows = await fetch(
        """
        SELECT * FROM tasks t
        WHERE t.workflow_id = $1
          AND t.status IN ('queued', 'blocked')
          AND NOT EXISTS (
              SELECT 1 FROM tasks d
              WHERE d.id = ANY(t.depends_on) AND d.status <> 'completed'
          )
        ORDER BY t.priority, t.created_at
        LIMIT $2
        """,
        workflow_id, limit,
    )
    return [dict(r) for r in rows]


async def claim(task_id: UUID) -> bool:
    """Take a task, once.

    The status guard in the WHERE clause is what makes this safe to call
    from two places at the same time: the second caller updates no rows
    and is told so, rather than both running the same work.
    """
    result = await execute(
        """
        UPDATE tasks SET status = 'running', attempts = attempts + 1,
                         started_at = COALESCE(started_at, now()), updated_at = now()
        WHERE id = $1 AND status IN ('queued', 'blocked')
        """,
        task_id,
    )
    return result.endswith("1")


async def complete(task_id: UUID, result: Any, confidence: float | None = None) -> None:
    await execute(
        """
        UPDATE tasks SET status = 'completed', result = $2, confidence = $3,
                         finished_at = now(), updated_at = now()
        WHERE id = $1
        """,
        task_id, result, confidence,
    )


async def fail(task_id: UUID, reason: str, *, terminal: bool) -> None:
    """Mark a failure, and decide whether it can be tried again.

    A task that has attempts left goes back to queued; one that has not
    is finished. That bound is what stops a persistent failure from
    becoming an endless retry loop, which is the failure mode that turns
    one broken agent into a spent budget.
    """
    await execute(
        """
        UPDATE tasks
        SET status = CASE
                WHEN $3 OR attempts >= max_attempts THEN 'failed'
                ELSE 'queued' END,
            failure_reason = $2,
            finished_at = CASE
                WHEN $3 OR attempts >= max_attempts THEN now() ELSE NULL END,
            updated_at = now()
        WHERE id = $1
        """,
        task_id, reason, terminal,
    )


async def await_approval(task_id: UUID, reason: str) -> None:
    await execute(
        "UPDATE tasks SET status = 'waiting_approval', failure_reason = $2, "
        "updated_at = now() WHERE id = $1",
        task_id, reason,
    )


async def cancel_workflow(workflow_id: UUID, reason: str) -> int:
    """Stop everything not already finished."""
    result = await execute(
        """
        UPDATE tasks SET status = 'cancelled', failure_reason = $2, updated_at = now()
        WHERE workflow_id = $1 AND status NOT IN ('completed','failed','cancelled')
        """,
        workflow_id, reason,
    )
    await execute(
        "UPDATE workflows SET status = 'cancelled', failure_reason = $2, "
        "finished_at = now(), updated_at = now() WHERE id = $1",
        workflow_id, reason,
    )
    return int(result.rsplit(" ", 1)[-1])


async def workflow_tasks(workflow_id: UUID) -> list[dict]:
    rows = await fetch(
        "SELECT * FROM tasks WHERE workflow_id = $1 ORDER BY created_at",
        workflow_id,
    )
    return [dict(r) for r in rows]


async def set_workflow_status(
    workflow_id: UUID, status: str, result: Any = None, failure: str | None = None
) -> None:
    await execute(
        """
        UPDATE workflows SET status = $2, result = COALESCE($3, result),
               failure_reason = COALESCE($4, failure_reason),
               finished_at = CASE WHEN $2 IN ('completed','failed','cancelled')
                                  THEN now() ELSE finished_at END,
               updated_at = now()
        WHERE id = $1
        """,
        workflow_id, status,
        result,
        failure,
    )


async def get_workflow(workflow_id: UUID) -> dict | None:
    row = await fetchrow("SELECT * FROM workflows WHERE id = $1", workflow_id)
    return dict(row) if row else None
