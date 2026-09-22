"""How each model has actually performed, per capability.

Section 15 of the brief: the router should learn which model is good at
what, from measurements rather than from opinion.

Everything here is a query over `agent_metrics`, which has recorded
success, failure, latency, cost and confidence since the agent
foundation existed. Until migration 011 it did not record WHO produced
them, so none of this was answerable about data JARVIS was already
collecting.

**Three rules, all of them about not fooling yourself with numbers.**

*A handful of runs is not evidence.* Two failures out of two is a
hundred per cent and means nothing. Below `ENOUGH` the answer is "not
enough to say", which is a real answer and the honest one.

*Recent, not lifetime.* A model that was bad in March and is good now is
good now. A window means a fix shows up instead of being averaged away
by its own history.

*The owner's instruction is not a datapoint.* This is consulted only
when he has NOT named a model. A hard override that quietly lost to a
success rate would be the exact silent substitution the whole preference
system exists to prevent.
"""
import logging
from dataclasses import dataclass

from app.db import fetch

logger = logging.getLogger("jarvis.performance")

# How far back to look. Long enough to accumulate a signal, short enough
# that a model fixed last week is not still being judged on last month.
WINDOW_DAYS = 30

# Below this, say so rather than guessing.
ENOUGH = 8

# A success rate under this, with enough runs behind it, is bad enough
# to route around.
POOR = 0.6


@dataclass(frozen=True)
class Record:
    """One model's measured history on one capability."""
    provider: str
    model: str
    tier: str | None
    runs: int
    successes: int
    avg_latency_ms: float | None
    avg_cost_inr: float | None
    avg_confidence: float | None

    @property
    def success_rate(self) -> float:
        return self.successes / self.runs if self.runs else 0.0

    @property
    def enough(self) -> bool:
        return self.runs >= ENOUGH

    def as_detail(self) -> dict:
        return {
            "provider": self.provider, "model": self.model, "tier": self.tier,
            "runs": self.runs, "success_rate": round(self.success_rate, 3),
            "avg_latency_ms": (round(self.avg_latency_ms)
                               if self.avg_latency_ms else None),
            "avg_cost_inr": (round(self.avg_cost_inr, 4)
                             if self.avg_cost_inr else None),
            "avg_confidence": (round(self.avg_confidence, 2)
                               if self.avg_confidence else None),
            "enough_to_judge": self.enough,
        }


async def history(capability: str | None = None) -> list[Record]:
    """What each model has done, newest window only.

    Never raises. A router that cannot route because the statistics
    table was slow is worse than a router with no statistics.
    """
    where = "WHERE created_at > now() - ($1 || ' days')::interval AND model IS NOT NULL"
    args: list = [str(WINDOW_DAYS)]
    if capability:
        where += " AND capability = $2"
        args.append(capability)

    try:
        rows = await fetch(
            f"""
            SELECT provider, model, MAX(tier) AS tier,
                   COUNT(*) FILTER (WHERE metric = 'success')            AS runs,
                   COALESCE(SUM(value) FILTER (WHERE metric = 'success'), 0) AS wins,
                   AVG(value) FILTER (WHERE metric = 'latency_ms')       AS latency,
                   AVG(value) FILTER (WHERE metric = 'cost_inr')         AS cost,
                   AVG(value) FILTER (WHERE metric = 'confidence')       AS confidence
              FROM agent_metrics
              {where}
             GROUP BY provider, model
            """,
            *args,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("Could not read model performance: %s", exc)
        return []

    out = []
    for row in rows:
        runs = int(row["runs"] or 0)
        if not runs:
            continue
        out.append(Record(
            provider=row["provider"] or "unknown",
            model=row["model"],
            tier=row["tier"],
            runs=runs,
            successes=int(row["wins"] or 0),
            avg_latency_ms=float(row["latency"]) if row["latency"] else None,
            avg_cost_inr=float(row["cost"]) if row["cost"] else None,
            avg_confidence=float(row["confidence"]) if row["confidence"] else None,
        ))
    out.sort(key=lambda r: (-r.success_rate, r.avg_latency_ms or 0))
    return out


def worst_of(records: list[Record]) -> Record | None:
    """The one to route around, out of records already fetched.

    Returns the WORST qualifying record rather than the best, because
    what the router does with this is escalate away from it. "Who is
    good" is a different and much harder question -- it needs the models
    to have been tried on comparable work -- and pretending to answer it
    from this data would be the kind of confident wrongness that is
    worse than no answer.

    Separate from `struggling` below, and public, so that the dashboard
    panel showing this can call it instead of deciding for itself. It
    had its own version for about an hour; the two disagreed, because
    "the first poor one in a list sorted by success rate" is the BEST of
    the poor ones and this is the worst. A browser check caught it. Two
    implementations of one judgement is one too many.
    """
    judged = [r for r in records if r.enough]
    if not judged:
        return None
    worst = min(judged, key=lambda r: r.success_rate)
    return worst if worst.success_rate < POOR else None


async def struggling(capability: str) -> Record | None:
    """The model currently doing badly at this, if one is and we can tell."""
    return worst_of(await history(capability))


async def report(capability: str | None = None) -> dict:
    """For the dashboard, and for answering "why did it pick that"."""
    records = await history(capability)
    judged = [r for r in records if r.enough]
    return {
        "window_days": WINDOW_DAYS,
        "enough_runs": ENOUGH,
        "models": [r.as_detail() for r in records],
        "said": (
            "Nothing has been measured yet."
            if not records else
            f"{len(records)} model(s) measured; "
            + (f"{len(judged)} with enough runs to judge."
               if judged else
               f"none with the {ENOUGH} runs needed to judge one.")
        ),
    }
