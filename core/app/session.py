"""Staying signed in, via an ordinary login cookie.

The bug this fixes: the sign-in was held only in the page's memory, so
reloading the page -- or the service redeploying -- silently signed the
owner out. It looked like sign-in had never worked, when in fact it had
worked and then been forgotten.

A cookie survives reloads, which is the entire point. It carries who you
are and when it expires, signed with a secret only this server knows.
Signing is what makes it safe: the contents are readable, but altering
them -- to claim a different identity, or extend the expiry -- breaks the
signature and the cookie is refused.

The cookie carries `email_verified` alongside the address because the
owner check needs it. Without it, a session issued to an email-identified
owner could not be re-checked against the owner rule on later requests,
and changing OWNER_EMAIL would not lock out sessions issued to the
previous owner until they expired on their own.
"""
import base64
import hashlib
import hmac
import json
import time

COOKIE_NAME = "jarvis_session"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sign(payload_b64: str, secret: str) -> str:
    digest = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).digest()
    return _b64(digest)


def create_session(
    uid: str,
    email: str | None,
    email_verified: bool,
    secret: str,
    days: int,
) -> str:
    payload = {
        "uid": uid,
        "email": email,
        "email_verified": bool(email_verified),
        "exp": int(time.time()) + days * 86400,
    }
    payload_b64 = _b64(json.dumps(payload, separators=(",", ":")).encode())
    return f"{payload_b64}.{_sign(payload_b64, secret)}"


def read_session(cookie: str, secret: str) -> dict | None:
    """The session's contents, or None if it isn't trustworthy.

    Every failure returns None alike -- tampered, expired, malformed,
    signed with a different secret. A caller has no reason to treat those
    differently, and saying which one failed would tell an attacker how
    close they got.

    An empty secret is refused outright. That is not a real secret, and
    accepting it would mean anyone could mint a cookie of their own using
    the same empty string.
    """
    if not secret:
        return None

    try:
        payload_b64, signature = cookie.split(".", 1)
    except ValueError:
        return None

    # Constant-time comparison: a plain == leaks, through how long it takes
    # to fail, how much of a guessed signature was right.
    if not hmac.compare_digest(signature, _sign(payload_b64, secret)):
        return None

    try:
        payload = json.loads(_unb64(payload_b64))
    except (ValueError, json.JSONDecodeError):
        return None

    if not isinstance(payload, dict) or not payload.get("uid"):
        return None
    if payload.get("exp", 0) < time.time():
        return None

    return payload
