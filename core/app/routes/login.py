"""Turning a sign-in into a session that survives reloading the page.

The page sends the signed-in identity here once; the server checks it and
replies with a login cookie. Everything after that is an ordinary
cookie-authenticated request, which is what makes the sign-in stick.

Why this exists at all: before it, the sign-in was held in a JavaScript
variable. That is page memory, and page memory dies on every reload --
including the automatic reload after each redeploy. The owner really had
signed in; the browser had simply forgotten by the time they tried to send
a message.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import audit
from app import session as session_lib
from app.auth import CurrentUser, get_current_user, is_owner, verify_token
from app.config import get_settings

logger = logging.getLogger("jarvis.login")

router = APIRouter()


class SignInRequest(BaseModel):
    credential: str


async def _try_audit(**kwargs) -> None:
    """Record the sign-in, but never let recording it decide the outcome.

    A database hiccup should not be able to lock the owner out, and it
    should not be able to quietly turn a refusal into an error that looks
    like something else. Either way the decision above has already been
    made; this only writes it down.
    """
    try:
        await audit.log_audit(**kwargs)
    except Exception as exc:  # noqa: BLE001 - deliberately never fatal
        logger.warning("Could not write sign-in to the audit log: %s", exc)


@router.post("/auth/session", include_in_schema=False)
async def start_session(body: SignInRequest) -> JSONResponse:
    """Exchange a Google or Firebase sign-in token for a login cookie.

    Accepts either kind of token, because the page offers two ways in and
    both should end in the same place. `verify_token` works out which it
    is and checks it against the right set of Google's public keys.
    """
    settings = get_settings()

    claims = verify_token(body.credential, settings)

    if not settings.owner_uid and not settings.owner_email:
        raise HTTPException(
            status_code=500,
            detail="No owner is configured on this server, so it cannot tell "
            "who is allowed in. Set OWNER_EMAIL in Render and redeploy -- "
            "see /docs/DEPLOYMENT.md step 2.",
        )

    if not is_owner(claims, settings):
        who = claims.get("email") or "that account"
        logger.warning("Refused sign-in for %s", who)
        await _try_audit(
            actor=f"google:{who}",
            action="sign_in_refused_not_owner",
            category="high_risk",
            outcome="refused",
        )
        raise HTTPException(
            status_code=403,
            detail=f"You signed in as {who}, but this JARVIS is set up for a "
            "different account. Only its owner can use it.",
        )

    if not settings.session_secret:
        raise HTTPException(
            status_code=500,
            detail="This server has no SESSION_SECRET, so it cannot issue a "
            "login that lasts. See /docs/DEPLOYMENT.md step 4g.",
        )

    await _try_audit(
        actor=f"user:{claims.get('email') or claims['uid']}",
        action="sign_in",
        category="low_risk",
        approved_by=f"user:{claims['uid']}",
        outcome="success",
    )

    token = session_lib.create_session(
        uid=claims["uid"],
        email=claims.get("email"),
        email_verified=bool(claims.get("email_verified")),
        secret=settings.session_secret,
        days=settings.session_days,
    )

    response = JSONResponse({"ok": True, "email": claims.get("email")})
    response.set_cookie(
        session_lib.COOKIE_NAME,
        token,
        max_age=settings.session_days * 86400,
        # httponly: unreadable to JavaScript, so a scripting flaw on the
        # page cannot walk off with the session.
        httponly=True,
        # secure: only ever sent over HTTPS. Off only for a plain-http run
        # on your own machine -- see COOKIE_SECURE in config.py.
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return response


@router.post("/auth/logout", include_in_schema=False)
async def logout() -> JSONResponse:
    response = JSONResponse({"ok": True})
    response.delete_cookie(session_lib.COOKIE_NAME, path="/")
    return response


@router.get("/v1/me", include_in_schema=False)
async def me(user: CurrentUser = Depends(get_current_user)) -> dict:
    """Who the server thinks you are.

    The page asks this on load instead of guessing from what it remembers,
    so what it shows is the server's answer rather than its own hopeful
    idea of the state.
    """
    return {"uid": user.uid, "email": user.email}
