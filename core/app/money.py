"""What came in and what went out.

The owner asked for this on day one -- walk in, be told what was done,
what was earned, what was spent. Tasks and model spend were measurable;
income was not, so the briefing said "NOT TRACKED AT ALL" in those words
rather than leaving a blank for a model to fill.

It is recorded from what the owner says, because that is how they use
JARVIS. "Got forty thousand from the Bengaluru shoot" is a sentence they
would say anyway; a form is a thing that does not get filled in. Bank and
invoice imports can write to the same table later.

Two rules hold the whole thing up.

**Only a stated figure becomes a row.** No inferring an amount from
context, no rounding a vague one into a number. A ledger that guesses is
worse than no ledger, because it looks like arithmetic.

**Every row can be found and undone.** A misheard "forty thousand" for
"four thousand" is one tap to remove, and each row keeps a link to the
exact words it was read out of.
"""
import logging
from decimal import Decimal
from uuid import UUID

from app.db import execute, fetch, fetchrow

logger = logging.getLogger("jarvis.money")

DIRECTIONS = {"in", "out"}


async def record(
    direction: str, amount_inr: Decimal | float | str, what: str,
    category: str | None = None, occurred_on=None,
    source: str = "stated", said_in: UUID | None = None,
) -> dict | None:
    """Write one movement of money. Returns the row, or None if refused.

    Refuses rather than raises: this runs in the background after a reply
    has already gone out, and a malformed figure should cost a missing
    row, not an error the owner cannot act on.
    """
    if direction not in DIRECTIONS:
        logger.warning("Refusing a money row with direction %r", direction)
        return None
    try:
        amount = Decimal(str(amount_inr)).quantize(Decimal("0.01"))
    except Exception:  # noqa: BLE001
        logger.warning("Refusing a money row with amount %r", amount_inr)
        return None
    if amount <= 0 or not (what or "").strip():
        return None

    row = await fetchrow(
        """
        INSERT INTO money_events (direction, amount_inr, what, category,
                                  occurred_on, source, said_in)
        VALUES ($1,$2,$3,$4,COALESCE($5, CURRENT_DATE),$6,$7)
        RETURNING *
        """,
        direction, amount, what.strip()[:300], (category or None),
        occurred_on, source, said_in,
    )
    return dict(row) if row else None


async def recent(limit: int = 20) -> list[dict]:
    rows = await fetch(
        "SELECT * FROM money_events ORDER BY occurred_on DESC, created_at DESC "
        "LIMIT $1",
        limit,
    )
    return [dict(r) for r in rows]


async def forget(event_id: UUID) -> bool:
    """Remove a row outright.

    Deleted rather than hidden, unlike memories. A ledger that quietly
    keeps a figure you told it to remove is a ledger whose totals you
    cannot check against your own bank.
    """
    result = await execute("DELETE FROM money_events WHERE id = $1", event_id)
    return result.endswith("1")


async def totals() -> dict:
    """This month and today, in and out. All measured; none inferred."""
    row = await fetchrow(
        """
        SELECT
          COALESCE(SUM(amount_inr) FILTER (
            WHERE direction = 'in'
              AND date_trunc('month', occurred_on) = date_trunc('month', CURRENT_DATE)
          ), 0) AS month_in,
          COALESCE(SUM(amount_inr) FILTER (
            WHERE direction = 'out'
              AND date_trunc('month', occurred_on) = date_trunc('month', CURRENT_DATE)
          ), 0) AS month_out,
          COALESCE(SUM(amount_inr) FILTER (
            WHERE direction = 'in' AND occurred_on = CURRENT_DATE
          ), 0) AS today_in,
          COALESCE(SUM(amount_inr) FILTER (
            WHERE direction = 'out' AND occurred_on = CURRENT_DATE
          ), 0) AS today_out,
          count(*) AS entries
        FROM money_events
        """
    )
    data = {k: float(v) for k, v in dict(row).items() if k != "entries"}
    data["entries"] = int(row["entries"])
    data["month_net"] = round(data["month_in"] - data["month_out"], 2)
    return data


def parse(entries) -> list[dict]:
    """Turn what the extraction pass returned into rows worth writing.

    Strict on purpose. Anything without a direction, a positive amount and
    a description is dropped rather than repaired -- a guessed figure in a
    ledger is the one mistake this module exists to avoid.
    """
    out: list[dict] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        direction = str(entry.get("direction") or "").strip().lower()
        if direction not in DIRECTIONS:
            continue
        try:
            amount = Decimal(str(entry.get("amount_inr")).replace(",", ""))
        except Exception:  # noqa: BLE001
            continue
        what = str(entry.get("what") or "").strip()
        if amount <= 0 or not what:
            continue
        out.append({
            "direction": direction, "amount_inr": amount, "what": what,
            "category": (str(entry.get("category")).strip()[:40]
                         if entry.get("category") else None),
        })
    return out
