"""Changes JARVIS has proposed, and what the owner decided.

Two steps on purpose. Planning costs one model call and produces
something to read; building costs a call per file and produces a branch
to review. The owner decides between them whether it is worth it, which
is the same shape as the Tasks tab's plan-then-run.
"""
import asyncio
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import system_control
from app.auth import CurrentUser, get_current_user
from app.config import get_settings
from app.dev import director, records, repo

router = APIRouter()
logger = logging.getLogger("jarvis.routes.dev")

_RUNNING: set[asyncio.Task] = set()


def _detach(coro, what: str) -> None:
    task = asyncio.create_task(coro)
    _RUNNING.add(task)

    def _done(finished: asyncio.Task) -> None:
        _RUNNING.discard(finished)
        if not finished.cancelled() and finished.exception():
            logger.error("%s ended in an exception: %s", what, finished.exception())

    task.add_done_callback(_done)


class PlanIn(BaseModel):
    brief: str = ""
    title: str = ""
    attachment_id: UUID | None = None


@router.get("/v1/dev", include_in_schema=False)
async def state(
    user: CurrentUser = Depends(get_current_user), settings=Depends(get_settings)
) -> dict:
    """Whether this deployment can build changes, and what it has proposed.

    The first half matters: a brief that becomes a plan and then discovers
    there is no git repository has spent money for nothing, so the page
    can say up front that the button will not work and why.
    """
    return {"repo": await repo.state(settings),
            "requests": await records.recent(20)}


@router.post("/v1/dev/plan", include_in_schema=False)
async def plan(
    body: PlanIn,
    user: CurrentUser = Depends(get_current_user),
    settings=Depends(get_settings),
) -> dict:
    """Read a brief and say what it would take. Writes nothing."""
    await system_control.refuse_if_stopped()

    brief = body.brief.strip()
    title = body.title.strip()
    if body.attachment_id:
        from app import attachments

        found = await attachments.get(body.attachment_id)
        if found is None:
            raise HTTPException(status_code=404, detail="No such attachment.")
        brief = found["content"]
        title = title or found["filename"]

    if not brief:
        raise HTTPException(status_code=400, detail="There is no brief to read.")

    request = await director.begin(
        brief, title, f"user:{user.email or user.uid}",
        body.attachment_id, settings,
    )
    request["id"] = str(request["id"])
    return request


@router.post("/v1/dev/{request_id}/build", include_in_schema=False)
async def build(
    request_id: UUID,
    user: CurrentUser = Depends(get_current_user),
    settings=Depends(get_settings),
) -> dict:
    """Write the plan into a branch. Returns immediately; it takes minutes."""
    await system_control.refuse_if_stopped()

    request = await records.get(request_id)
    if request is None:
        raise HTTPException(status_code=404, detail="No such change request.")
    if request["state"] not in ("planned", "failed"):
        raise HTTPException(
            status_code=409,
            detail=f"That change is {request['state']}, not waiting to be built.",
        )

    _detach(
        director.build(request_id, f"user:{user.email or user.uid}", settings),
        f"build {request_id}",
    )
    return {"id": str(request_id), "state": "building",
            "note": "Writing the files and running the tests. A few minutes."}


@router.get("/v1/dev/{request_id}", include_in_schema=False)
async def one(
    request_id: UUID, user: CurrentUser = Depends(get_current_user)
) -> dict:
    row = await records.get(request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such change request.")
    row["id"] = str(row["id"])
    return row


@router.post("/v1/dev/{request_id}/{decision}", include_in_schema=False)
async def decide(
    request_id: UUID, decision: str,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Approve or discard.

    Approving records that the owner read it and accepted it. It does not
    merge: the branch is his, and a system that could merge its own
    changes is one tap away from a system that does.
    """
    if decision not in records.DECISIONS:
        raise HTTPException(status_code=404, detail="Approve it or discard it.")
    await system_control.refuse_if_stopped()

    row = await records.decide(
        request_id, decision, f"user:{user.email or user.uid}")
    if row is None:
        raise HTTPException(
            status_code=409, detail="That is not waiting for a decision.")
    row["id"] = str(row["id"])
    row["note"] = (
        f"Recorded. The branch {row['branch']} is yours to merge; JARVIS "
        f"does not merge its own changes."
        if decision == "approve" else
        f"Discarded. The branch {row['branch']} is still there if you want "
        f"to look at it, and yours to delete."
    )
    return row
