"""What happened, in enough detail to replay it afterwards.

The trace exists to answer, about any finished workflow: why was this
agent chosen, what was it told, which model ran, what did it cost, what
came back, what failed, who approved. Reconstructing that from
application logs is guesswork; these are rows.

It is also the substrate JARVIS Scientist will read. Building it now
costs a table and a function call per step. Retrofitting it later would
mean instrumenting code that has already grown around not having it.
"""
from decimal import Decimal
from uuid import UUID

from app.db import execute, fetch


async def record(
    kind: str,
    *,
    workflow_id: UUID | None = None,
    task_id: UUID | None = None,
    capability: str | None = None,
    detail: dict | None = None,
    cost_inr: Decimal | None = None,
    duration_ms: int | None = None,
) -> None:
    """Write one trace row. Never raises.

    Telemetry that can break the thing it observes is worse than no
    telemetry: it turns a working system into a broken one at exactly the
    moment something interesting is happening.
    """
    try:
        await execute(
            """
            INSERT INTO agent_events
                (workflow_id, task_id, capability, kind, detail, cost_inr, duration_ms)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            """,
            workflow_id, task_id, capability, kind,
            detail or {}, cost_inr, duration_ms,
        )
    except Exception:  # noqa: BLE001 - observing must never break the observed
        pass


async def trace(workflow_id: UUID) -> list[dict]:
    """Everything that happened in one workflow, in order."""
    rows = await fetch(
        """
        SELECT kind, capability, detail, cost_inr, duration_ms, created_at, task_id
        FROM agent_events WHERE workflow_id = $1 ORDER BY created_at, id
        """,
        workflow_id,
    )
    return [dict(r) for r in rows]
