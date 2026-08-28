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
from fastapi import Cookie, Header, HTTPException
from google.auth import jwt as google_jwt

from app import session
from app.config import Settings, get_settings

# Google publishes the public keys for both kinds of sign-in token it
# issues. Both are public data -- no authentication needed to read them.
#
# There are two, because there are two kinds of token:
#   * Firebase ID tokens, from the Firebase sign-in flow;
#   * Google ID tokens, from Google's own in-page sign-in button.
# They are signed with different keys and carry different claims, so each
# is checked against its own key set. JARVIS accepts either, which means a
# problem with one route never locks the owner out entirely.
FIREBASE_CERTS_URL = (
    "https://www.googleapis.com/robot/v1/metadata/x509/"
    "securetoken@system.gserviceaccount.com"
)
GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v1/certs"

FIREBASE_ISSUER_PREFIX = "https://securetoken.google.com/"
GOOGLE_ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})

# Google rotates these keys occasionally. Re-fetching on every request
# would add a network round trip to every message; caching for 30 minutes
# keeps verification fast while picking up rotations well within the time
# Google keeps old keys valid.
_CERT_CACHE_SECONDS = 1800

_certs_lock = threading.Lock()
_certs: dict[str, tuple[dict, float]] = {}


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


def _get_certs(url: str = FIREBASE_CERTS_URL, force_refresh: bool = False) -> dict:
    with _certs_lock:
        cached = _certs.get(url)
        if cached and not force_refresh:
            certs, fetched_at = cached
            if (time.time() - fetched_at) < _CERT_CACHE_SECONDS:
                return certs

        response = requests.get(url, timeout=10)
        response.raise_for_status()
        certs = response.json()
        _certs[url] = (certs, time.time())
        return certs


def _decode_with_retry(token: str, certs_url: str, audience: str) -> dict:
    """Check a token's signature, audience and expiry against Google's keys.

    Key rotation is the common innocent reason a signature fails to match
    a cached key, so a rotation shouldn't lock the owner out until the
    cache expires: refetch once before rejecting.
    """

    def _decode(certs: dict) -> dict:
        return google_jwt.decode(token, certs=certs, audience=audience)

    try:
        return _decode(_get_certs(certs_url))
    except ValueError:
        try:
            return _decode(_get_certs(certs_url, force_refresh=True))
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=f"Invalid token: {exc}") from exc
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not reach Google to verify the sign-in: {exc}",
        ) from exc


def verify_firebase_token(token: str, settings: Settings) -> dict:
    """A token from the Firebase sign-in flow.

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

    claims = _decode_with_retry(token, FIREBASE_CERTS_URL, project_id)

    if claims.get("iss") != f"{FIREBASE_ISSUER_PREFIX}{project_id}":
        raise HTTPException(status_code=401, detail="Token has the wrong issuer.")
    if not claims.get("sub"):
        raise HTTPException(status_code=401, detail="Token has no subject.")

    claims["uid"] = claims["sub"]
    return claims


def verify_google_token(token: str, settings: Settings) -> dict:
    """A token from Google's own in-page sign-in button.

    Same three checks as above, against the key set and issuer Google uses
    for these, and with our OAuth client ID as the audience -- a token
    minted for somebody else's app must not be accepted here.
    """
    client_id = settings.google_client_id
    if not client_id:
        raise HTTPException(
            status_code=500,
            detail="GOOGLE_CLIENT_ID is not configured on this server, so "
            "this sign-in cannot be verified. See /docs/DEPLOYMENT.md.",
        )

    claims = _decode_with_retry(token, GOOGLE_CERTS_URL, client_id)

    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise HTTPException(status_code=401, detail="Token has the wrong issuer.")
    if not claims.get("sub"):
        raise HTTPException(status_code=401, detail="Token has no subject.")

    claims["uid"] = claims["sub"]
    return claims


def verify_token(token: str, settings: Settings) -> dict:
    """Accept either kind of sign-in token.

    Which one it is can be read off the issuer without trusting anything:
    the signature is still checked afterwards either way. Reading it first
    just avoids attempting the wrong verification and reporting a
    misleading reason for the failure.
    """
    try:
        unverified = google_jwt.decode(token, verify=False)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}") from exc

    issuer = unverified.get("iss", "")
    if issuer in GOOGLE_ISSUERS:
        return verify_google_token(token, settings)
    if issuer.startswith(FIREBASE_ISSUER_PREFIX):
        return verify_firebase_token(token, settings)
    raise HTTPException(
        status_code=401,
        detail="Token was not issued by a sign-in method this server accepts.",
    )


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


def _require_owner_configured(settings: Settings) -> None:
    if not settings.owner_uid and not settings.owner_email:
        raise HTTPException(
            status_code=500,
            detail="No owner is configured on this server yet, so it can't "
            "tell who is allowed in. Set OWNER_EMAIL (or OWNER_UID) -- see "
            "/docs/DEPLOYMENT.md step 2.",
        )


def _refuse_if_not_owner(identity: dict, settings: Settings) -> None:
    _require_owner_configured(settings)
    if not is_owner(identity, settings):
        raise HTTPException(
            status_code=403,
            detail="This JARVIS instance is configured for a single owner "
            "and this account is not it.",
        )


async def get_current_user(
    authorization: str | None = Header(default=None),
    jarvis_session: str | None = Cookie(default=None),
) -> CurrentUser:
    """Who is making this request.

    Two ways in, checked in this order:

      1. The login cookie, set when the owner signed in. This is the one
         the test console uses, and the reason a sign-in now survives
         reloading the page. It is checked first because it is the common
         case and costs nothing -- no network call to Google.
      2. An `Authorization: Bearer <id token>` header, for anything
         calling the API directly rather than through a browser.

    Both end at the same place: a verified identity that must be the
    owner's. The cookie's identity is re-checked against the owner rule on
    every request rather than trusted for its whole lifetime, so changing
    OWNER_EMAIL takes effect immediately instead of whenever old sessions
    happen to expire.
    """
    settings = get_settings()

    if settings.dev_mode:
        return CurrentUser(uid=settings.owner_uid or "dev-owner", email="dev@localhost")

    if jarvis_session:
        payload = session.read_session(jarvis_session, settings.session_secret)
        if payload:
            _refuse_if_not_owner(payload, settings)
            return CurrentUser(uid=payload["uid"], email=payload.get("email"))
        # A cookie that doesn't check out is treated as no cookie at all,
        # so a stale one left over from an earlier deployment falls through
        # to the header below instead of hard-failing the request.

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Not signed in. Open the JARVIS page and tap the Google "
            "sign-in button, or send a Google/Firebase ID token as "
            "'Authorization: Bearer <token>'.",
        )

    claims = verify_token(authorization.removeprefix("Bearer ").strip(), settings)
    _refuse_if_not_owner(claims, settings)

    return CurrentUser(uid=claims["uid"], email=claims.get("email"))
