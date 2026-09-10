"""The Media Company, as far as the owner's screen is concerned.

Four things happen here and nothing else: look for something worth
making, make one, read what came back, and say yes or no to it. Every one
of them goes through the same runtime, the same permissions and the same
budget as everything else in JARVIS -- this is a window, not a second
engine.

Scanning and producing both return immediately and finish behind the
request. A production run is five agents and several minutes; holding a
phone's connection open for that means a spinner, a proxy timeout, and no
way to lock the screen and come back to it.
"""
import asyncio
import logging
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import system_control
from app.agents import tasks
from app.auth import CurrentUser, get_current_user
from app.media import brands, director, records, workflow

router = APIRouter()
logger = logging.getLogger("jarvis.routes.media")

# Live background runs, held so the event loop does not collect them.
# asyncio keeps only a weak reference to a bare create_task, so a
# fire-and-forget production can vanish mid-step -- which looks exactly
# like a piece that hung.
_RUNNING: set[asyncio.Task] = set()


def _detach(coro, what: str) -> None:
    task = asyncio.create_task(coro)
    _RUNNING.add(task)

    def _done(finished: asyncio.Task) -> None:
        _RUNNING.discard(finished)
        if not finished.cancelled() and finished.exception():
            logger.error("%s ended in an exception: %s", what, finished.exception())

    task.add_done_callback(_done)


class ScanIn(BaseModel):
    theme: str
    brand: str = brands.AI_MEDIA


class ProduceIn(BaseModel):
    topic: str
    brand: str = brands.AI_MEDIA
    budget_inr: Decimal | None = None


@router.get("/v1/media/brands", include_in_schema=False)
async def list_brands(user: CurrentUser = Depends(get_current_user)) -> list[dict]:
    """The two properties, so the panel names them the same way the agents do."""
    return [
        {"id": key, "name": spec["name"], "one_line": spec["one_line"]}
        for key, spec in brands.BRANDS.items()
    ]


@router.post("/v1/media/scan", include_in_schema=False)
async def scan(
    body: ScanIn, user: CurrentUser = Depends(get_current_user)
) -> dict:
    """Look for something worth making. Commits to nothing and makes nothing."""
    await system_control.refuse_if_stopped()
    theme = body.theme.strip()
    if not theme:
        raise HTTPException(status_code=400, detail="Say what to look at.")

    from app.agents import orchestrator

    brand = body.brand if body.brand in brands.BRANDS else brands.AI_MEDIA
    workflow_id = await orchestrator.start(
        f"Scout opportunities: {theme}", f"user:{user.email or user.uid}",
        steps=workflow.opportunity_scan(theme, brand),
    )
    rows = await tasks.workflow_tasks(workflow_id)
    if rows:
        _detach(orchestrator.advance(workflow_id), f"scan {workflow_id}")

    return {"workflow_id": str(workflow_id), "theme": theme,
            "state": "running" if rows else "failed"}


@router.get("/v1/media/scan/{workflow_id}", include_in_schema=False)
async def scan_result(
    workflow_id: UUID, user: CurrentUser = Depends(get_current_user)
) -> dict:
    """What the scan found, if it has finished finding it."""
    wf = await tasks.get_workflow(workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail="No such scan.")
    rows = await tasks.workflow_tasks(workflow_id)
    found = workflow.opportunities_of(rows)
    return {
        "workflow_id": str(workflow_id),
        "state": wf["status"],
        "reason": wf.get("failure_reason") or "",
        "opportunities": found,
        # An empty queue from a finished scan is an answer, not a gap.
        # Saying nothing here is how "nothing was worth covering" reads as
        # "it broke".
        "nothing_worth_covering": bool(
            wf["status"] == "completed" and not found
        ),
    }


@router.post("/v1/media/produce", include_in_schema=False)
async def produce(
    body: ProduceIn, user: CurrentUser = Depends(get_current_user)
) -> dict:
    """Take one topic through research, verification, strategy, script, review.

    Publishes nothing. The furthest this goes on its own is a package
    sitting at 'ready' with the reviewer's notes attached.
    """
    await system_control.refuse_if_stopped()
    topic = body.topic.strip()
    if not topic:
        raise HTTPException(status_code=400, detail="Say what it should be about.")

    started = await director.begin(
        topic, f"user:{user.email or user.uid}", body.brand, body.budget_inr
    )
    if started["state"] != "failed":
        _detach(
            director.run(UUID(started["workflow_id"]), topic, started["brand"]),
            f"produce {started['workflow_id']}",
        )
    return started


@router.get("/v1/media/pieces", include_in_schema=False)
async def pieces(
    limit: int = 20, state: str | None = None,
    user: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    return await records.recent(limit, state)


@router.get("/v1/media/economics", include_in_schema=False)
async def economics(
    days: int = 30, user: CurrentUser = Depends(get_current_user)
) -> dict:
    """What making things cost, in both currencies, never added together."""
    return await records.economics(min(max(days, 1), 365))


@router.get("/v1/media/pieces/{piece_id}", include_in_schema=False)
async def piece(
    piece_id: UUID, user: CurrentUser = Depends(get_current_user)
) -> dict:
    row = await records.get(piece_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such piece.")

    # Which step it has reached. Without this, a piece being made shows as
    # "being made" and nothing else, so a run that has stalled looks
    # exactly like one that is working -- which is how ten minutes of
    # nothing became a question rather than something visible.
    if row.get("workflow_id"):
        row["steps"] = [
            {"capability": t["capability"], "status": t["status"],
             "started_at": t["started_at"], "finished_at": t["finished_at"],
             "failure_reason": t["failure_reason"]}
            for t in await tasks.workflow_tasks(row["workflow_id"])
        ]
    return row


@router.post("/v1/media/pieces/{piece_id}/{decision}", include_in_schema=False)
async def decide(
    piece_id: UUID, decision: str,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Approve it or discard it.

    Approving records that the owner said yes. It does not publish
    anything -- nothing in this system can -- and it is the evidence a
    later, more autonomous stage would have to stand on.
    """
    if decision not in records.DECISIONS:
        raise HTTPException(status_code=404, detail="Approve it or discard it.")
    await system_control.refuse_if_stopped()

    row = await records.decide(piece_id, decision, f"user:{user.email or user.uid}")
    if row is None:
        # Either it does not exist or it was already decided. Both mean
        # the same thing to whoever tapped: there is nothing to decide.
        raise HTTPException(
            status_code=409, detail="That is not waiting for a decision."
        )
    return row
