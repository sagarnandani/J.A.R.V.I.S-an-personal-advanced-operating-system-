"""Authentication.

Stage 0 is explicitly single-user: only the owner may ever call the
authenticated endpoints. A request must carry a valid Firebase ID token
(`Authorization: Bearer <token>`) whose `uid` matches `settings.owner_uid`.
Firebase verifies the token was genuinely issued to a signed-in user; the
owner_uid check is defense-in-depth on top of that, in case a second
account is ever added to the Firebase project by mistake.

`dev_mode` bypasses all of this and returns a fixed fake user. It exists
so the API can be developed and tested without live Firebase credentials.
It must be false in any deployment reachable from the internet -- see the
loud warning in main.py's startup log when it's on.
"""
from dataclasses import dataclass

import firebase_admin
from fastapi import Header, HTTPException
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials

from app.config import get_settings

_firebase_app: firebase_admin.App | None = None


@dataclass
class CurrentUser:
    uid: str
    email: str | None


def init_firebase() -> None:
    global _firebase_app
    settings = get_settings()
    if settings.dev_mode:
        return
    if _firebase_app is not None:
        return
    if settings.firebase_service_account_path:
        cred = credentials.Certificate(settings.firebase_service_account_path)
        _firebase_app = firebase_admin.initialize_app(cred)
    else:
        # Falls back to Application Default Credentials, which is what
        # Cloud Run provides automatically via its service account -- no
        # JSON file needed in that environment.
        _firebase_app = firebase_admin.initialize_app()


async def get_current_user(
    authorization: str | None = Header(default=None),
) -> CurrentUser:
    settings = get_settings()

    if settings.dev_mode:
        return CurrentUser(uid=settings.owner_uid or "dev-owner", email="dev@localhost")

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing bearer token. Sign in and send your Firebase ID token "
            "as 'Authorization: Bearer <token>'.",
        )

    token = authorization.removeprefix("Bearer ").strip()
    try:
        decoded = firebase_auth.verify_id_token(token)
    except Exception as exc:  # firebase_admin raises several distinct types
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}") from exc

    uid = decoded["uid"]
    if not settings.owner_uid:
        raise HTTPException(
            status_code=500,
            detail="OWNER_UID is not configured on the server yet. "
            "See /docs/DEPLOYMENT.md step 'Set the owner'.",
        )
    if uid != settings.owner_uid:
        raise HTTPException(
            status_code=403,
            detail="This JARVIS instance is configured for a single owner "
            "and this account is not it.",
        )

    return CurrentUser(uid=uid, email=decoded.get("email"))
