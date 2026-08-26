"""Public, unauthenticated health check -- "how do I know if it's running"."""
from fastapi import APIRouter

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
    }


@router.get("/public/firebase-config")
async def firebase_config() -> dict:
    """Public Firebase web config for the Stage 0 test console.

    These values identify the Firebase project to the client SDK; they are
    not secrets (the same way a website's domain name isn't a secret) --
    see Firebase's own docs on this. The actual secret (the service
    account key used server-side to verify tokens) is never exposed here.
    """
    settings = get_settings()
    return {
        "apiKey": settings.firebase_api_key,
        "authDomain": settings.firebase_auth_domain,
        "projectId": settings.firebase_project_id,
        "appId": settings.firebase_app_id,
    }
