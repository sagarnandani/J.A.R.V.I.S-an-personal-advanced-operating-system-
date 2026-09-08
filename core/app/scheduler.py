"""Work that happens without being asked.

Until now JARVIS acted only when the owner typed or spoke. This is the
difference between a tool and an operating system: research ready before
he is awake, a check that ran overnight.

It is also the first code in this project that spends the owner's money
with nobody watching, and that shapes almost every decision in here.

**It stops well before the ceiling.** Scheduled work is capped at a share
of the monthly budget, not all of it. Unattended spend must never be what
exhausts a budget the owner then cannot use for their own conversations.

That guard is real code with a currently inert trigger: on Google's free
tier Gemini is priced at zero, so recorded spend never reaches a
percentage of anything. It starts working the moment a paid provider is
configured. Until then the bound that actually holds is a count -- a
daily ceiling on unattended runs, across every schedule -- because a
limit that cannot fire is not a limit, and describing it as one would be
a guarantee that is not one.

**Each schedule has a daily cap.** A loop that runs every minute is a bill
nobody notices until the month ends.

**Late is not skipped, but late is once.** A free-tier server sleeps, so a
job due at seven may not be looked at until nine. Running it late is
almost always what the owner wanted; running it four times because four
slots passed is not.

**One runner at a time.** An advisory lock, because Render starts the
replacement instance before stopping the old one, so two schedulers
overlapping is normal rather than exotic.
"""
import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from app.db import execute, fetch, fetchrow

logger = logging.getLogger("jarvis.scheduler")

# Any constant, as long as every instance agrees.
_LOCK_KEY = 8_314_559_777

# How often the in-process loop looks. A minute is finer than any schedule
# this supports, and costs one indexed query.
TICK_SECONDS = 60


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - a bad name must not stop the clock
        logger.warning("Unknown timezone %r; falling back to UTC.", name)
        return ZoneInfo("UTC")


def next_due(
    hour: int, minute: int, days: list[int], tz: str, after: datetime | None = None
) -> datetime:
    """The next moment this schedule should run, in UTC.

    `days` is ISO weekdays, 1=Monday..7=Sunday; empty means every day.
    Computed in the owner's own timezone, because "seven in the morning"
    is a wall clock and daylight saving is not the owner's problem.
    """
    zone = _zone(tz)
    now = (after or datetime.now(timezone.utc)).astimezone(zone)
    wanted = set(days or [])

    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    # At most a week out: seven tries covers every weekday pattern.
    for _ in range(8):
        if not wanted or candidate.isoweekday() in wanted:
            return candidate.astimezone(timezone.utc)
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


async def create(
    objective: str, hour: int, minute: int = 0,
    days: list[int] | None = None, tz: str = "Asia/Kolkata",
    max_per_day: int = 2, created_by: str = "owner",
) -> dict:
    row = await fetchrow(
        """
        INSERT INTO schedules (objective, hour, minute, days_of_week, timezone,
                               max_per_day, next_due_at, created_by)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
        RETURNING *
        """,
        objective.strip(), hour, minute, list(days or []), tz, max_per_day,
        next_due(hour, minute, days or [], tz), created_by,
    )
    return dict(row)


async def listing() -> list[dict]:
    rows = await fetch("SELECT * FROM schedules ORDER BY next_due_at")
    return [dict(r) for r in rows]


async def set_enabled(schedule_id: UUID, enabled: bool) -> bool:
    result = await execute(
        "UPDATE schedules SET enabled = $2, updated_at = now() WHERE id = $1",
        schedule_id, enabled,
    )
    return result.endswith("1")


async def delete(schedule_id: UUID) -> bool:
    result = await execute("DELETE FROM schedules WHERE id = $1", schedule_id)
    return result.endswith("1")


async def _budget_left_for_scheduled_work(settings) -> tuple[bool, str]:
    """Whether unattended work may spend anything right now.

    Deliberately stricter than the owner's own ceiling. If a schedule can
    spend the last rupee of the month, the owner discovers it by being
    unable to talk to their own assistant.
    """
    from app.budget import get_month_spend_inr

    ceiling = Decimal(settings.monthly_budget_inr)
    share = ceiling * Decimal(settings.scheduler_budget_percent) / Decimal(100)
    spent = await get_month_spend_inr()
    if spent >= share:
        return False, (
            f"scheduled work is paused: Rs.{spent:.2f} spent this month, and "
            f"unattended work stops at Rs.{share:.2f} of the Rs.{ceiling:.0f} "
            f"ceiling so the rest stays yours"
        )
    return True, ""


async def _claim(row: dict, settings) -> bool:
    """Take this schedule, or leave it for whoever else is looking.

    The whole decision is one statement, so two runners cannot both see
    the same due schedule and both act on it. Everything the guard needs
    -- due, enabled, under its daily cap -- is checked in the WHERE.
    """
    today = datetime.now(_zone(row["timezone"])).date()
    result = await execute(
        """
        UPDATE schedules
           SET last_run_at = now(),
               runs_today = CASE WHEN runs_today_on = $2 THEN runs_today + 1 ELSE 1 END,
               runs_today_on = $2,
               next_due_at = $3,
               updated_at = now()
         WHERE id = $1
           AND enabled
           AND next_due_at <= now()
           AND (runs_today_on <> $2 OR runs_today < max_per_day)
        """,
        row["id"], today,
        next_due(row["hour"], row["minute"], list(row["days_of_week"] or []),
                 row["timezone"]),
    )
    return result.endswith("1")


async def _record(schedule_id: UUID, workflow_id, outcome: str) -> None:
    await execute(
        "UPDATE schedules SET last_workflow_id = $2, last_outcome = $3, "
        "updated_at = now() WHERE id = $1",
        schedule_id, workflow_id, outcome[:500],
    )


async def run_due(settings) -> list[dict]:
    """Run everything that is due. Returns what was run.

    Never raises. A scheduler that can take the process down turns a
    missed job into an outage.
    """
    if not settings.scheduler_enabled:
        return []

    from app.db import get_pool

    ran: list[dict] = []
    try:
        from app import system_control

        # Checked here rather than only at the endpoints: this is the one
        # path that spends money with nobody watching, so it is the one
        # that most needs to notice the stop button.
        #
        # Inside the try on purpose. If the database cannot be reached,
        # this raises, and the except below turns that into "nothing ran"
        # -- which is the right way for a safety check to fail. Unable to
        # tell whether the owner has stopped it is not a reason to spend
        # their money finding out.
        if await system_control.is_stopped():
            logger.info("Emergency stop is on; no scheduled work will run.")
            return []

        async with get_pool().acquire() as conn:
            got = await conn.fetchval("SELECT pg_try_advisory_lock($1)", _LOCK_KEY)
            if not got:
                return []   # another instance is already on it
            try:
                due = await conn.fetch(
                    "SELECT * FROM schedules WHERE enabled AND next_due_at <= now() "
                    "ORDER BY next_due_at LIMIT 5"
                )
                if not due:
                    return []

                affordable, why = await _budget_left_for_scheduled_work(settings)
                if not affordable:
                    logger.warning("%s", why)
                    for row in due:
                        await _record(row["id"], None, why)
                    return []

                for row in due:
                    # Counted before each run, not once for the batch. A
                    # tick can pick up several due schedules at once, and
                    # checking the ceiling only at the top let the whole
                    # batch through however high the count already was.
                    if await _at_daily_ceiling(conn, settings):
                        why = (
                            f"scheduled work is paused: the day's ceiling of "
                            f"{settings.scheduler_max_runs_per_day} unattended "
                            f"runs has been reached"
                        )
                        logger.warning("%s", why)
                        await _record(row["id"], None, why)
                        continue
                    outcome = await _run_one(dict(row), settings)
                    if outcome:
                        ran.append(outcome)
            finally:
                await conn.execute("SELECT pg_advisory_unlock($1)", _LOCK_KEY)
    except Exception as exc:  # noqa: BLE001 - a missed job, never an outage
        logger.warning("Scheduler tick failed: %s", exc)
    return ran


async def _at_daily_ceiling(conn, settings) -> bool:
    """Has unattended work already had its share of the day?

    A count rather than a cost, because on Google's free tier the cost is
    always zero and a share of the monthly ceiling is never reached. The
    money guard is the real one on a paid provider; this is the one that
    holds today.

    The day is the owner's day, not the server's. A schedule stamps
    `runs_today_on` with its own local date, so counting against Postgres'
    CURRENT_DATE -- which is UTC -- matched nothing for the five and a
    half hours each evening when the two dates differ, and the ceiling
    quietly stopped existing exactly when unattended work is most likely
    to be running.
    """
    today = datetime.now(_zone(settings.timezone)).date()
    ran_today = await conn.fetchval(
        "SELECT COALESCE(SUM(runs_today), 0) FROM schedules "
        "WHERE runs_today_on = $1",
        today,
    )
    return int(ran_today or 0) >= settings.scheduler_max_runs_per_day


async def _run_one(row: dict, settings) -> dict | None:
    from app import audit
    from app.agents import orchestrator

    if not await _claim(row, settings):
        return None   # somebody else took it, or its daily cap is spent

    # How late this is, said plainly. A free-tier server sleeps, so a job
    # due at seven may not be looked at until nine, and the owner should
    # be told when they read the result rather than assuming it is fresh.
    late = datetime.now(timezone.utc) - row["next_due_at"]
    late_minutes = max(0, int(late.total_seconds() // 60))

    logger.info("Running scheduled work: %s", row["objective"][:120])
    try:
        result = await orchestrator.run(row["objective"], "schedule")
        outcome = result.get("status", "unknown")
        await _record(row["id"], result.get("workflow_id"), outcome)
        await audit.log_audit(
            actor="schedule", action="scheduled_work", category="low_risk",
            outcome=f"{outcome} ({late_minutes} min late)",
        )
        return {"schedule_id": str(row["id"]), "objective": row["objective"],
                "status": outcome, "workflow_id": result.get("workflow_id"),
                "late_minutes": late_minutes}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Scheduled work failed: %s", exc)
        await _record(row["id"], None, f"failed: {exc}")
        return {"schedule_id": str(row["id"]), "objective": row["objective"],
                "status": "failed", "workflow_id": None,
                "late_minutes": late_minutes}


async def loop(settings) -> None:
    """Check every minute while the process is awake.

    Not the only trigger, and deliberately so: on a free tier the process
    sleeps, and a loop that is not running cannot notice anything. The
    cron endpoint exists for something outside to wake it. Both feed the
    same `run_due`, which is safe to call from either.
    """
    while True:
        try:
            await asyncio.sleep(TICK_SECONDS)
            await run_due(settings)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("Scheduler loop error: %s", exc)
