"""The two sides of a sidecar: the machine's, and the owner's.

The machine's endpoints authenticate with a bearer token it was given at
pairing, NOT with the owner's session. That separation is the point: a
sidecar is not the owner, cannot use the dashboard, and cannot see
anything except the jobs addressed to it.

The owner's endpoints authenticate the ordinary way and are the only
place capabilities are ever granted.
"""
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from app import sidecars, system_control
from app.auth import CurrentUser, get_current_user

logger = logging.getLogger("jarvis.routes.sidecars")

router = APIRouter()


# --- the machine's side ----------------------------------------------------

async def _calling_sidecar(authorization: str = Header(default="")) -> sidecars.Sidecar:
    """Which machine is calling. One message for every kind of failure.

    A revoked token and a wrong token say the same thing, because telling
    them apart tells somebody probing which of their guesses was closer.
    """
    token = ""
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    found = await sidecars.authenticate(token)
    if found is None:
        raise HTTPException(status_code=401, detail="Not a known sidecar.")
    return found


class PairIn(BaseModel):
    code: str
    reported: dict = {}


class PollIn(BaseModel):
    reported: dict = {}
    limit: int = 5


class DoneIn(BaseModel):
    ok: bool
    result: dict | None = None
    error: str = ""


@router.post("/v1/sidecar/pair", include_in_schema=False)
async def pair(body: PairIn) -> dict:
    """The one unauthenticated call: a code, exchanged for a token.

    Deliberately not behind the owner's session -- the machine doing the
    pairing is not signed in and should not have to be. The code is what
    proves the owner authorised it, which is why it is short-lived and
    single-use.
    """
    try:
        return await sidecars.redeem_pairing(body.code, body.reported)
    except sidecars.SidecarError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/v1/sidecar/poll", include_in_schema=False)
async def poll(
    body: PollIn, me: sidecars.Sidecar = Depends(_calling_sidecar)
) -> dict:
    """"What should I do?" -- asked by the machine, never pushed to it."""
    await sidecars.seen(me.id, body.reported)

    # The emergency stop reaches the other machines too. A paused JARVIS
    # that still has hands is not paused.
    if await system_control.is_stopped():
        return {"jobs": [], "paused": True,
                "said": "JARVIS is stopped. Nothing is being handed out."}

    if me.status != "active":
        return {"jobs": [], "paused": True,
                "said": f"This sidecar is {me.status}."}

    await sidecars.expire_old()
    jobs = await sidecars.next_jobs(me, max(1, min(body.limit, 20)))
    return {"jobs": [{**j, "id": str(j["id"])} for j in jobs], "paused": False,
            "you_may": sorted(me.capabilities)}


@router.post("/v1/sidecar/jobs/{job_id}", include_in_schema=False)
async def finished(
    job_id: UUID, body: DoneIn,
    me: sidecars.Sidecar = Depends(_calling_sidecar),
) -> dict:
    """What came back. Accepted only from the machine it was given to."""
    took = await sidecars.finish(me, job_id, ok=body.ok,
                                 result=body.result, error=body.error)
    if not took:
        raise HTTPException(
            status_code=404,
            detail="That job is not yours, or is not still out.")
    return {"ok": True}


# --- the owner's side ------------------------------------------------------

class OfferIn(BaseModel):
    name: str
    capabilities: list[str] = []
    kind: str = "native"


class ThisBrowserIn(BaseModel):
    name: str = ""
    capabilities: list[str] = []
    reported: dict = {}


class StatusIn(BaseModel):
    status: str


@router.get("/v1/sidecars", include_in_schema=False)
async def listing(user: CurrentUser = Depends(get_current_user)) -> dict:
    """Which machines JARVIS can reach, and what each one may do."""
    return await sidecars.state()


@router.post("/v1/sidecars/pairings", include_in_schema=False)
async def offer(
    body: OfferIn, user: CurrentUser = Depends(get_current_user)
) -> dict:
    """A code to type into the sidecar on the other machine.

    The capabilities are chosen here, before that machine has said
    anything at all -- so what a sidecar may do is never a function of
    what it claims to be.
    """
    await system_control.refuse_if_stopped()
    try:
        return await sidecars.offer_pairing(
            body.name, body.capabilities, kind=body.kind,
            by=f"user:{user.email or user.uid}")
    except sidecars.SidecarError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/v1/sidecars/this-browser", include_in_schema=False)
async def pair_this_browser(
    body: ThisBrowserIn, user: CurrentUser = Depends(get_current_user)
) -> dict:
    """Pair the tab the owner is looking at. One tap, no code.

    The code exists so a machine JARVIS has never met can prove he
    authorised it -- read off one screen, typed into another. A browser
    has nothing to type it into and does not need to: it is already
    signed in as him. Asking him to copy a code from a page into that
    same page is ceremony, and ceremony that achieves nothing is how
    people learn to click past security.
    """
    await system_control.refuse_if_stopped()
    try:
        return await sidecars.pair_this_browser(
            body.name, body.capabilities, reported=body.reported,
            by=f"user:{user.email or user.uid}")
    except sidecars.SidecarError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/v1/sidecars/{name}/status", include_in_schema=False)
async def set_status(
    name: str, body: StatusIn,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Pause, resume, or revoke a machine. Revoking is final."""
    try:
        changed = await sidecars.set_status(
            name, body.status, by=f"user:{user.email or user.uid}")
    except sidecars.SidecarError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not changed:
        raise HTTPException(status_code=404, detail="No such sidecar.")
    return {"ok": True, "name": name, "status": body.status}


@router.get("/v1/sidecars/jobs", include_in_schema=False)
async def jobs(
    limit: int = 25, user: CurrentUser = Depends(get_current_user)
) -> dict:
    """What has been asked of the other machines, and what is waiting."""
    return {"recent": await sidecars.recent(limit),
            "waiting": await sidecars.waiting_for_owner()}


@router.post("/v1/sidecars/jobs/{job_id}/approve", include_in_schema=False)
async def approve(
    job_id: UUID, user: CurrentUser = Depends(get_current_user)
) -> dict:
    """Let one waiting job through. One job, never a category."""
    await system_control.refuse_if_stopped()
    if not await sidecars.approve(job_id, by=f"user:{user.email or user.uid}"):
        raise HTTPException(
            status_code=404, detail="That job is not waiting for you.")
    return {"ok": True}
