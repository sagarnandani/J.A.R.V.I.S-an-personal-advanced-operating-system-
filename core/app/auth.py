"""Authentication.

Stage 0 is explicitly single-user: only the owner may call the
authenticated endpoints. A request must carry a valid Firebase ID token
(`Authorization: Bearer <token>`) belonging to the owner.

**No Google credentials are needed to run this.** Firebase ID tokens are
signed by Google, and the keys needed to check that signature are
published openly. We fetch those public keys and verify the token
ourselves, rather than using Firebase's Admin library, which would demand
either a secret key file or a Google-hosted environment. That choice is
what lets JARVIS run on Render, a home server, or anywhere else without a
Google service account -- the portability the architecture doc asks for
(section D), and one fewer secret to store and protect.

`dev_mode` bypasses all of this and returns a fixed fake user, so the API
can be developed and tested without live Firebase. It must be false in any
deployment reachable from the internet.
"""
import threading
import time
from dataclasses import dataclass

import requests
from fastapi import Header, HTTPException
from google.auth import jwt as google_jwt

from app.config import Settings, get_settings

# Where Google publishes the public keys for Firebase ID tokens. Public
# data -- no authentication required to read it.
FIREBASE_CERTS_URL = (
    "https://www.googleapis.com/robot/v1/metadata/x509/"
    "securetoken@system.gserviceaccount.com"
)
# Google rotates these keys occasionally. Re-fetching on every request
# would add a network round trip to every message; caching for 30 minutes
# keeps verification fast while picking up rotations well within the time
# Google keeps old keys valid.
_CERT_CACHE_SECONDS = 1800

_certs_lock = threading.Lock()
_certs: dict | None = None
_certs_fetched_at: float = 0.0


@dataclass
class CurrentUser:
    uid: str
    email: str | None


def init_firebase() -> None:
    """Kept as a no-op so startup code reads the same.

    Verification needs no initialisation any more -- there is no client to
    build and no credential to load.
    """
    return None


def _get_certs(force_refresh: bool = False) -> dict:
    global _certs, _certs_fetched_at
    with _certs_lock:
        fresh = (
            _certs is not None
            and (time.time() - _certs_fetched_at) < _CERT_CACHE_SECONDS
        )
        if fresh and not force_refresh:
            return _certs

        response = requests.get(FIREBASE_CERTS_URL, timeout=10)
        response.raise_for_status()
        _certs = response.json()
        _certs_fetched_at = time.time()
        return _certs


def verify_firebase_token(token: str, settings: Settings) -> dict:
    """Check the token is genuinely Google's, and genuinely for us.

    Three things must hold, and all three matter:
      * the signature matches one of Google's published keys (proves
        Google issued it and it hasn't been tampered with);
      * the audience is our Firebase project (proves it was issued for
        THIS app -- a valid token for some other Firebase project must
        not open our door);
      * the issuer is Firebase's token service for our project.

    Signature, audience and expiry are checked by the library. Issuer is
    checked here, because the library does not.
    """
    project_id = settings.firebase_project_id
    if not project_id:
        raise HTTPException(
            status_code=500,
            detail="FIREBASE_PROJECT_ID is not configured on this server, so "
            "sign-ins cannot be verified. See /docs/DEPLOYMENT.md.",
        )

    def _decode(certs: dict) -> dict:
        return google_jwt.decode(token, certs=certs, audience=project_id)

    try:
        claims = _decode(_get_certs())
    except ValueError:
        # A key rotation is the common innocent cause of a signature that
        # doesn't match a cached key. Refetch once before rejecting, so a
        # rotation doesn't lock the owner out until the cache expires.
        try:
            claims = _decode(_get_certs(force_refresh=True))
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=f"Invalid token: {exc}") from exc
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not reach Google to verify the sign-in: {exc}",
        ) from exc

    expected_issuer = f"https://securetoken.google.com/{project_id}"
    if claims.get("iss") != expected_issuer:
        raise HTTPException(status_code=401, detail="Token has the wrong issuer.")

    if not claims.get("sub"):
        raise HTTPException(status_code=401, detail="Token has no subject.")

    # Firebase puts the user id in `sub`; expose it as `uid` so the rest of
    # the code reads naturally.
    claims["uid"] = claims["sub"]
    return claims


def is_owner(decoded_token: dict, settings) -> bool:
    """Is this verified sign-in the owner's?

    Kept separate from the request handling so the rule can be tested
    directly -- this is the check standing between the whole system and
    anyone else who finds the URL, so it shouldn't only be exercised
    through a live Firebase login.

    Matching on email requires the provider to have verified the address.
    Without that check, someone could register an unverified account
    claiming the owner's address and be let straight in.
    """
    if settings.owner_uid and decoded_token.get("uid") == settings.owner_uid:
        return True

    if settings.owner_email:
        email = decoded_token.get("email")
        if (
            email
            and decoded_token.get("email_verified")
            and email.strip().lower() == settings.owner_email.strip().lower()
        ):
            return True

    return False


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

    claims = verify_firebase_token(authorization.removeprefix("Bearer ").strip(), settings)

    if not settings.owner_uid and not settings.owner_email:
        raise HTTPException(
            status_code=500,
            detail="No owner is configured on this server yet, so it can't "
            "tell who is allowed in. Set OWNER_EMAIL (or OWNER_UID) -- see "
            "/docs/DEPLOYMENT.md step 2.",
        )

    if not is_owner(claims, settings):
        raise HTTPException(
            status_code=403,
            detail="This JARVIS instance is configured for a single owner "
            "and this account is not it.",
        )

    return CurrentUser(uid=claims["uid"], email=claims.get("email"))
