"""Public, unauthenticated health check -- "how do I know if it's running"."""
from fastapi import APIRouter, Request

from app import system_control
from app.config import get_settings

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    stopped = await system_control.is_stopped()
    return {
        "status": "ok",
        "emergency_stop": stopped,
        "dev_mode": get_settings().dev_mode,
        # Which code is running and whether the database matches it. Both
        # questions have now cost real time to answer from a phone, and
        # both are one query away from the thing already being asked.
        "schema": await _schema(),
    }


async def _schema() -> dict:
    """Never the reason /health fails.

    A health check that goes down because a diagnostic went down is worse
    than no diagnostic: it turns a working deployment into one that looks
    dead.
    """
    try:
        from app.db import get_pool
        from app.migrate import state

        return await state(get_pool())
    except Exception:  # noqa: BLE001
        return {"state": "unknown"}


@router.get("/public/firebase-config")
async def firebase_config(request: Request) -> dict:
    """Public Firebase web config for the Stage 0 test console.

    These values identify the Firebase project to the client SDK; they are
    not secrets, the same way a website's domain name isn't a secret.

    `authDomain` is deliberately reported as *this* server's own host
    rather than the project's firebaseapp.com address. Sign-in only
    completes if the app and Firebase's sign-in helper share a domain --
    Safari blocks the cross-domain storage the split version depends on --
    and app/routes/auth_proxy.py serves that helper from here to make them
    match. Deriving it from the incoming request means this keeps working
    wherever JARVIS is deployed, with nothing to reconfigure.
    """
    settings = get_settings()
    host = request.headers.get("host") or settings.firebase_auth_domain
    return {
        "apiKey": settings.firebase_api_key,
        "authDomain": host,
        "projectId": settings.firebase_project_id,
        "appId": settings.firebase_app_id,
        "googleClientId": settings.google_client_id,
    }
