"""Connecting the accounts JARVIS may post to.

Four routes and one of them is unauthenticated by necessity: LinkedIn
redirects the owner's browser back here after he consents, and a
redirect carries no session of ours. The `state` is what stands in for
one -- generated here, stored, single-use, ten minutes.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from app import system_control
from app.auth import CurrentUser, get_current_user
from app.config import get_settings
from app.social import linkedin

logger = logging.getLogger("jarvis.routes.social")

router = APIRouter()


def _redirect_uri(request: Request) -> str:
    """Where LinkedIn sends him back.

    Built from the address he is actually on, so it is right on
    localhost, on a home server and behind a domain without three
    settings that disagree with each other. It must match what is typed
    into the LinkedIn app exactly, and the dashboard shows it for
    copying rather than making him work it out.
    """
    return str(request.url_for("linkedin_connected"))


@router.get("/v1/social", include_in_schema=False)
async def state(request: Request,
                user: CurrentUser = Depends(get_current_user)) -> dict:
    """What JARVIS can post to, and what is missing if it cannot."""
    said = await linkedin.state(get_settings())
    # Shown so he can paste it into the LinkedIn app rather than working
    # out what this server calls itself.
    said["redirect_uri"] = _redirect_uri(request)
    return said


@router.post("/v1/social/linkedin/connect", include_in_schema=False)
async def connect(request: Request,
                  user: CurrentUser = Depends(get_current_user)) -> dict:
    """The consent URL to open. He taps it; LinkedIn asks him; it returns."""
    await system_control.refuse_if_stopped()
    try:
        url = await linkedin.start(
            _redirect_uri(request), get_settings(),
            by=f"user:{user.email or user.uid}")
    except linkedin.NotConnected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"url": url}


@router.get("/v1/social/linkedin/connected", include_in_schema=False,
            name="linkedin_connected")
async def linkedin_connected(request: Request, code: str = "", state: str = "",
                             error: str = "",
                             error_description: str = "") -> HTMLResponse:
    """Where LinkedIn sends his browser back.

    Unauthenticated because a redirect carries no session. What proves
    this was asked for is the `state`, which JARVIS generated, stored,
    and will accept exactly once within ten minutes.
    """
    if error:
        return _page(f"LinkedIn said no: {error_description or error}", ok=False)
    if not code:
        return _page("LinkedIn sent no code back.", ok=False)

    try:
        account = await linkedin.finish(
            code, state, _redirect_uri(request), get_settings(),
            by="user:owner")
    except linkedin.NotConnected as exc:
        return _page(str(exc), ok=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("LinkedIn connection failed: %s", exc)
        return _page("That did not work. Try again from the Build tab.",
                     ok=False)

    return _page(
        f"Connected as {account.name or 'your account'}."
        + ("" if account.may_post else
           " It cannot post yet: add the 'Share on LinkedIn' product to your "
           "app and connect again."),
        ok=account.may_post)


@router.post("/v1/social/linkedin/disconnect", include_in_schema=False)
async def disconnect(user: CurrentUser = Depends(get_current_user)) -> dict:
    """Forget the account. JARVIS can no longer post as him."""
    return {"ok": await linkedin.disconnect()}


def _page(message: str, *, ok: bool) -> HTMLResponse:
    """One plain page. He is in a browser tab LinkedIn opened, not in the
    dashboard, so this has to stand on its own."""
    from html import escape

    return HTMLResponse(f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>JARVIS</title></head>
<body style="font-family:system-ui;background:#04121d;color:#dff1ff;
             display:grid;place-items:center;height:100vh;margin:0;
             text-align:center;padding:1.5rem">
<div><h1 style="font-size:1.1rem;color:{'#7fe0a8' if ok else '#ff9a9a'}">
{'Connected' if ok else 'Not connected'}</h1>
<p style="max-width:34rem;line-height:1.5">{escape(message)}</p>
<p style="opacity:.6;font-size:.85rem">You can close this tab and go back
to JARVIS.</p></div></body></html>""")
