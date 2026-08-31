"""What JARVIS can truthfully report about itself right now.

The owner wants to walk in and be told where things stand -- tasks done,
money made, money spent. Two of those do not exist yet: nothing tracks
income, and the task engine is Stage 1. So this reports what is actually
measured and states plainly what is not.

That last part is the point of the module. Handed a prompt with some
figures missing, a model will fill the gap with something plausible, and
a confident invented number about money is worse than no number at all.
Naming the gaps explicitly is what stops it.
"""
from datetime import datetime, timezone

from app.db import fetchrow


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
                    = date_trunc('month', now()))                      AS month_spend
        """
    )
    if row is None:
        return ""

    spend = float(row["month_spend"] or 0)
    ceiling = float(settings.monthly_budget_inr)
    now = datetime.now(timezone.utc)

    return (
        "Current status, measured (use these figures exactly; do not round "
        "them into vagueness or invent others):\n"
        f"- Date and time, UTC: {now:%A %d %B %Y, %H:%M}\n"
        f"- Exchanges today: {row['today_messages']}\n"
        f"- Long-term facts held about the owner: {row['facts']}\n"
        f"- Spent this month on model usage: Rs.{spend:.2f} of a "
        f"Rs.{ceiling:.0f} ceiling\n"
        "- Tasks completed: NOT TRACKED YET -- the task engine is not "
        "built. Say so if asked; do not estimate.\n"
        "- Money earned: NOT TRACKED AT ALL -- nothing records income. "
        "Say so if asked; never produce a figure."
    )
