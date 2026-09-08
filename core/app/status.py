"""What JARVIS can truthfully report about itself right now.

The owner wants to walk in and be told where things stand -- tasks done,
money made, money spent. Two of those are now real: work runs on a
schedule and every task records what it cost. The third is not. Nothing
records income, and this says so in those words rather than leaving a
gap for a model to fill.

That last part is the whole point of the module. Handed a prompt with a
figure missing, a model will supply something plausible, and a confident
invented number about money is worse than no number at all. Naming the
gaps explicitly is what stops it.

An earlier version of this told the model "the task engine is not built".
That went on being sent for a week after the task engine was built, so
JARVIS answered "nothing is tracked" while completed work sat in the
database. A hardcoded claim about the system's own capabilities goes
stale silently, which is why every line here is now a query.
"""
from datetime import datetime, timezone

from app.db import fetch, fetchrow


async def briefing(settings) -> str:
    """A short factual status line for the system prompt."""
    row = await fetchrow(
        """
        SELECT
            (SELECT count(*) FROM audit_log
              WHERE action IN ('llm_message_exchange', 'llm_voice_exchange')
                AND created_at >= date_trunc('day', now()))            AS today_messages,
            (SELECT count(*) FROM memories
              WHERE origin = 'inferred'
                AND (expires_at IS NULL OR expires_at > now()))        AS facts,
            (SELECT COALESCE(SUM(cost), 0) FROM audit_log
              WHERE date_trunc('month', created_at)
                    = date_trunc('month', now()))                      AS month_spend,
            -- Work JARVIS actually did, measured from the task rows
            -- rather than asserted.
            (SELECT count(*) FROM tasks
              WHERE status = 'completed'
                AND finished_at >= now() - interval '24 hours')        AS tasks_done,
            (SELECT count(*) FROM tasks
              WHERE status = 'failed'
                AND finished_at >= now() - interval '24 hours')        AS tasks_failed,
            (SELECT COALESCE(SUM(spend_inr), 0) FROM tasks
              WHERE finished_at >= now() - interval '24 hours')        AS tasks_spend,
            (SELECT count(*) FROM schedules WHERE enabled)             AS schedules_on
        """
    )
    if row is None:
        return ""

    spend = float(row["month_spend"] or 0)
    ceiling = float(settings.monthly_budget_inr)
    now = datetime.now(timezone.utc)

    lines = [
        "Current status, measured (use these figures exactly; do not round "
        "them into vagueness or invent others):",
        f"- Date and time, UTC: {now:%A %d %B %Y, %H:%M}",
        f"- Exchanges today: {row['today_messages']}",
        f"- Long-term facts held about the owner: {row['facts']}",
        f"- Spent this month on model usage: Rs.{spend:.2f} of a "
        f"Rs.{ceiling:.0f} ceiling",
        f"- Tasks completed in the last 24 hours: {row['tasks_done']}"
        + (f" ({row['tasks_failed']} failed)" if row["tasks_failed"] else "")
        + f", costing Rs.{float(row['tasks_spend'] or 0):.2f}",
        f"- Standing schedules: {row['schedules_on']}",
        "- Money earned: NOT TRACKED AT ALL -- nothing records income. "
        "Say so if asked; never produce a figure.",
    ]

    done = await recent_work()
    if done:
        lines.append("- Work finished since the owner was last here:")
        lines.extend(f"    * {item}" for item in done)

    return "\n".join(lines)


async def recent_work(limit: int = 5) -> list[str]:
    """What was finished while the owner was away, ready to be told.

    Only work they have not already been told about. A briefing that
    repeats yesterday's news every morning is one you stop listening to.
    """
    rows = await fetch(
        """
        SELECT w.objective, w.finished_at,
               COALESCE(SUM(t.spend_inr), 0) AS spend
          FROM workflows w
          JOIN tasks t ON t.workflow_id = w.id
         WHERE w.status = 'completed'
           AND w.finished_at IS NOT NULL
           AND w.finished_at > (SELECT last_seen_at FROM briefing_marks WHERE id)
         GROUP BY w.id, w.objective, w.finished_at
         ORDER BY w.finished_at DESC
         LIMIT $1
        """,
        limit,
    )
    return [
        f"{r['objective']} (finished {r['finished_at']:%H:%M UTC}, "
        f"Rs.{float(r['spend']):.2f})"
        for r in rows
    ]


async def mark_seen() -> None:
    """Note that the owner has now been told.

    Called after a briefing has actually gone out, not when it was built,
    so a message that failed on the way does not silently consume the
    news it was carrying.
    """
    from app.db import execute

    try:
        await execute("UPDATE briefing_marks SET last_seen_at = now() WHERE id")
    except Exception:  # noqa: BLE001 - never worth failing a reply over
        pass
