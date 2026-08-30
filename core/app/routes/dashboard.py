"""One request that fills the whole dashboard.

Every panel could fetch its own data, and on a laptop nobody would
notice. On a free-tier service talking to a hosted database, six panels
means six round trips before anything appears -- so the dashboard would
feel slow for reasons that have nothing to do with JARVIS thinking.

One endpoint, all the queries issued at once.

**Everything here is real.** No panel shows a number JARVIS cannot
actually measure. Where a thing does not exist yet -- scheduled tasks,
calendar events -- the dashboard says so rather than showing a plausible
figure, because a dashboard you cannot trust is worse than no dashboard.
"""
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from app import system_control
from app.auth import CurrentUser, get_current_user
from app.budget import get_budget_snapshot
from app.config import Settings, get_settings
from app.db import fetch, fetchrow
from app.facts import FACT_ORIGIN

router = APIRouter()


async def _counts() -> dict:
    row = await fetchrow(
        """
        SELECT
            count(*) FILTER (
                WHERE origin = $1 AND (expires_at IS NULL OR expires_at > now())
            ) AS facts,
            count(*) FILTER (
                WHERE origin IN ('stated', 'retrieved')
                  AND (expires_at IS NULL OR expires_at > now())
            ) AS turns,
            count(*) FILTER (
                WHERE expires_at IS NOT NULL AND expires_at <= now()
            ) AS forgotten,
            count(*) AS total
        FROM memories
        """,
        FACT_ORIGIN,
    )
    return dict(row) if row else {}


async def _today() -> dict:
    """Since midnight UTC.

    UTC rather than the owner's timezone, because the server has no idea
    what that is and guessing would make the number quietly wrong for a
    few hours each day. Labelled as UTC on the dashboard.
    """
    row = await fetchrow(
        """
        SELECT
            count(*) FILTER (WHERE action = 'llm_message_exchange') AS messages,
            count(*) FILTER (WHERE action = 'memory_learn')          AS learning_runs,
            COALESCE(SUM(cost), 0)                                   AS spend
        FROM audit_log
        WHERE created_at >= date_trunc('day', now())
        """
    )
    return dict(row) if row else {}


async def _history(days: int) -> list[dict]:
    """Messages per day, for the sparkline.

    generate_series gives every day in the range, so quiet days come back
    as zero rather than being missing. A gap-free series is the whole
    point: a chart that silently omits the days you did not use JARVIS
    would draw a flattering line rather than a true one.
    """
    rows = await fetch(
        """
        SELECT d::date AS day,
               COALESCE(c.n, 0) AS messages
        FROM generate_series(
                 date_trunc('day', now()) - ($1::int - 1) * interval '1 day',
                 date_trunc('day', now()),
                 interval '1 day'
             ) AS d
        LEFT JOIN (
            SELECT date_trunc('day', created_at) AS day, count(*) AS n
            FROM audit_log
            WHERE action = 'llm_message_exchange'
            GROUP BY 1
        ) c ON c.day = d
        ORDER BY d
        """,
        days,
    )
    return [{"day": r["day"].isoformat(), "messages": int(r["messages"])} for r in rows]


async def _activity(limit: int) -> list[dict]:
    rows = await fetch(
        """
        SELECT action, category, outcome, created_at
        FROM audit_log ORDER BY created_at DESC LIMIT $1
        """,
        limit,
    )
    return [dict(r) for r in rows]


async def _recent_facts(limit: int) -> list[dict]:
    rows = await fetch(
        """
        SELECT id, content, category, created_at
        FROM memories
        WHERE origin = $1 AND (expires_at IS NULL OR expires_at > now())
        ORDER BY created_at DESC LIMIT $2
        """,
        FACT_ORIGIN,
        limit,
    )
    return [dict(r) for r in rows]


@router.get("/v1/dashboard", include_in_schema=False)
async def dashboard(
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    stopped, counts, today, activity, facts, budget, history = await asyncio.gather(
        system_control.is_stopped(),
        _counts(),
        _today(),
        _activity(40),
        _recent_facts(40),
        get_budget_snapshot(settings),
        _history(7),
    )

    return {
        "now": datetime.now(timezone.utc).isoformat(),
        "owner": user.email or user.uid,
        "status": {
            "emergency_stop": stopped,
            "provider": settings.llm_provider,
            # Name the model that would actually answer. The old version
            # fell through to the Claude name for any non-Gemini provider,
            # so a deployment running the mock adapter displayed a real
            # model it was never going to call.
            "model": {
                "gemini": settings.gemini_model,
                "claude": settings.claude_model,
            }.get(settings.llm_provider.strip().lower(), "placeholder (no real model)"),
            "recall_enabled": settings.memory_recall_enabled,
            "facts_enabled": settings.memory_facts_enabled,
        },
        "today": {
            "messages": int(today.get("messages") or 0),
            "learning_runs": int(today.get("learning_runs") or 0),
            "spend_inr": float(today.get("spend") or 0),
        },
        "memory": {
            "facts": int(counts.get("facts") or 0),
            "turns": int(counts.get("turns") or 0),
            "forgotten": int(counts.get("forgotten") or 0),
            "total": int(counts.get("total") or 0),
            # What actually reaches the model on a message, so the limits
            # are visible rather than something you have to know about.
            "recall_turns_limit": settings.memory_recall_turns,
            "facts_limit": settings.memory_facts_limit,
        },
        "budget": {
            "spend_inr": float(budget.spend_inr),
            "ceiling_inr": float(budget.ceiling_inr),
            "percent_used": budget.percent_used,
            "status": budget.status,
            "month": budget.month,
        },
        "history": history,
        "activity": [
            {
                "action": a["action"],
                "category": a["category"],
                "outcome": a["outcome"],
                "at": a["created_at"].isoformat(),
            }
            for a in activity
        ],
        "facts": [
            {
                "id": str(f["id"]),
                "content": f["content"],
                "category": f["category"],
                "at": f["created_at"].isoformat(),
            }
            for f in facts
        ],
    }
