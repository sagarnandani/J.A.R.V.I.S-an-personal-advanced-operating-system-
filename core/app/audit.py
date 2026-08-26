"""Audit log writes.

Every autonomous or system action gets a row here (architecture doc,
section F/I). Stage 0 has exactly one action type -- handling a message --
which is always `category='low_risk'` and always auto-approved
(`approved_by=None`), per the Stage 0 brief section 4 step 5. Higher-risk
categories exist in the schema now so the Approval Engine (Stage 1+) has
somewhere to write without a migration.
"""
from decimal import Decimal
from uuid import UUID

from app.db import fetch, fetchrow

VALID_CATEGORIES = {"low_risk", "medium_risk", "high_risk"}


async def log_audit(
    actor: str,
    action: str,
    category: str,
    outcome: str | None = None,
    cost: Decimal | None = None,
    approved_by: str | None = None,
) -> UUID:
    if category not in VALID_CATEGORIES:
        raise ValueError(f"Unknown audit category: {category!r}")

    row = await fetchrow(
        """
        INSERT INTO audit_log (actor, action, category, approved_by, outcome, cost)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id
        """,
        actor,
        action,
        category,
        approved_by,
        outcome,
        cost,
    )
    assert row is not None
    return row["id"]


async def list_recent(limit: int = 20) -> list:
    return await fetch(
        "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT $1", limit
    )
