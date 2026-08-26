"""Budget guardrail (Stage 0 brief, section 5).

What this tracks: estimated Anthropic API cost, computed from the actual
token counts each call returns times a configurable price-per-token,
converted to INR at a configurable rate. This is a real calculation from
real usage, not a guess -- but it is still an *estimate*, not a live
billing feed, because Anthropic does not expose real-time billing via the
API. That trade-off is called out here per the brief's instruction to flag
it rather than skip it silently (also documented in /docs/BUDGET.md).

What this does NOT track: Cloud Run compute, Postgres, Firebase. Those are
expected to stay at $0 on free/scale-to-zero tiers at this usage level, but
confirming that means checking those dashboards directly -- integrating
real-time billing APIs for all three is out of scope for Stage 0.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from app.config import Settings
from app.db import fetchrow

WARN_50 = Decimal("0.5")
WARN_80 = Decimal("0.8")


def estimate_cost_inr(
    input_tokens: int, output_tokens: int, settings: Settings
) -> Decimal:
    usd = (
        Decimal(input_tokens) / Decimal(1_000_000) * settings.price_input_usd_per_1m
        + Decimal(output_tokens) / Decimal(1_000_000) * settings.price_output_usd_per_1m
    )
    return (usd * settings.usd_to_inr_rate).quantize(Decimal("0.000001"))


@dataclass
class BudgetSnapshot:
    month: str
    spend_inr: Decimal
    ceiling_inr: Decimal
    percent_used: float
    status: str
    note: str


async def get_month_spend_inr() -> Decimal:
    now = datetime.now(timezone.utc)
    row = await fetchrow(
        """
        SELECT COALESCE(SUM(cost), 0) AS total
        FROM audit_log
        WHERE date_trunc('month', created_at) = date_trunc('month', $1::timestamptz)
        """,
        now,
    )
    assert row is not None
    return Decimal(row["total"])


async def get_budget_snapshot(settings: Settings) -> BudgetSnapshot:
    now = datetime.now(timezone.utc)
    spend = await get_month_spend_inr()
    ceiling = settings.monthly_budget_inr
    fraction = (spend / ceiling) if ceiling > 0 else Decimal(0)

    if fraction >= 1:
        status = "exceeded"
        note = (
            "Estimated LLM spend has passed this month's ceiling. This "
            "covers Anthropic API usage only -- check Cloud Run/Supabase/"
            "Firebase dashboards separately for infra costs."
        )
    elif fraction >= WARN_80:
        status = "warn_80"
        note = "Over 80% of this month's LLM budget estimate has been used."
    elif fraction >= WARN_50:
        status = "warn_50"
        note = "Over 50% of this month's LLM budget estimate has been used."
    else:
        status = "ok"
        note = "Within budget."

    return BudgetSnapshot(
        month=now.strftime("%Y-%m"),
        spend_inr=spend,
        ceiling_inr=ceiling,
        percent_used=float(fraction * 100),
        status=status,
        note=note,
    )
