"""Money, attributed to the thing that spent it.

Cost is a primitive here rather than a report generated later. Every
model call is charged to a task, a task rolls up to a workflow, and a
workflow can be stopped for exceeding what it was given. Bolting this on
afterwards means the attribution is approximate, and an approximate
budget is one nobody trusts enough to enforce.
"""
from decimal import Decimal
from uuid import UUID

from app.budget import estimate_cost_inr, estimate_shadow_inr
from app.db import execute, fetchrow


def price(tokens_in: int, tokens_out: int, provider: str, settings) -> Decimal:
    """Reuses Stage 0's per-provider pricing, so there is one price list."""
    return estimate_cost_inr(tokens_in, tokens_out, settings, provider=provider)


def shadow(tokens_in: int, tokens_out: int, provider: str, settings) -> Decimal:
    """What the same work would have cost on a paid model.

    Kept strictly apart from `price`. On the free tier `price` is zero and
    correct; this is the number that lets one workflow be compared with
    another, and it must never be added to anything called spend.
    """
    return estimate_shadow_inr(tokens_in, tokens_out, settings, provider=provider)


async def charge(
    task_id: UUID, workflow_id: UUID | None, amount: Decimal,
    shadow_amount: Decimal = Decimal(0),
) -> None:
    """Record what was spent, and what it would have cost if billed.

    Two columns, never summed together. The workflow's `spend_inr` stays
    the real figure -- it is what the budget guard reads, and a guard that
    counts imaginary money would refuse real work.
    """
    if amount <= 0 and shadow_amount <= 0:
        return
    await execute(
        "UPDATE tasks SET spend_inr = spend_inr + $2, shadow_inr = shadow_inr + $3 "
        "WHERE id = $1",
        task_id, amount, shadow_amount,
    )
    if amount <= 0:
        return
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
