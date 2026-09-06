"""Money, attributed to the thing that spent it.

Cost is a primitive here rather than a report generated later. Every
model call is charged to a task, a task rolls up to a workflow, and a
workflow can be stopped for exceeding what it was given. Bolting this on
afterwards means the attribution is approximate, and an approximate
budget is one nobody trusts enough to enforce.
"""
from decimal import Decimal
from uuid import UUID

from app.budget import estimate_cost_inr
from app.db import execute, fetchrow


def price(tokens_in: int, tokens_out: int, provider: str, settings) -> Decimal:
    """Reuses Stage 0's per-provider pricing, so there is one price list."""
    return estimate_cost_inr(tokens_in, tokens_out, settings, provider=provider)


async def charge(task_id: UUID, workflow_id: UUID | None, amount: Decimal) -> None:
    if amount <= 0:
        return
    await execute(
        "UPDATE tasks SET spend_inr = spend_inr + $2 WHERE id = $1",
        task_id, amount,
    )
    if workflow_id:
        await execute(
            "UPDATE workflows SET spend_inr = spend_inr + $2, updated_at = now() "
            "WHERE id = $1",
            workflow_id, amount,
        )


async def remaining(workflow_id: UUID | None) -> Decimal | None:
    """What is left of a workflow's budget. None means no ceiling was set."""
    if workflow_id is None:
        return None
    row = await fetchrow(
        "SELECT budget_inr, spend_inr FROM workflows WHERE id = $1", workflow_id
    )
    if row is None or row["budget_inr"] is None:
        return None
    return Decimal(row["budget_inr"]) - Decimal(row["spend_inr"])


async def affordable(workflow_id: UUID | None, estimate: Decimal) -> bool:
    """Checked BEFORE spending, not after.

    Discovering an overspend afterwards is an audit trail, not a budget.
    """
    left = await remaining(workflow_id)
    return left is None or left >= estimate
