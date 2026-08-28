"""The login cookie must be unforgeable, and must expire.

These are the tests that matter for this file: the cookie is the only
thing standing between "signed in" and "not signed in" on every request
after the first, so a flaw here is a flaw in the front door. Each test
below is one way someone could try to walk through it.
"""
import base64
import json
import time

import pytest

from app.session import _b64, create_session, read_session

SECRET = "test-secret-not-used-anywhere-real"


def _payload_of(cookie: str) -> dict:
    body = cookie.split(".", 1)[0]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def test_roundtrip_returns_the_identity_it_was_given():
    cookie = create_session("uid-123", "owner@example.com", True, SECRET, 30)
    payload = read_session(cookie, SECRET)
    assert payload is not None
    assert payload["uid"] == "uid-123"
    assert payload["email"] == "owner@example.com"
    assert payload["email_verified"] is True


def test_rejects_a_cookie_signed_with_a_different_secret():
    cookie = create_session("uid-123", "owner@example.com", True, SECRET, 30)
    assert read_session(cookie, "some-other-secret") is None


def test_rejects_an_identity_swapped_for_someone_elses():
    """The attack this whole design exists to stop.

    The contents are readable, so anyone can see who the cookie belongs
    to. Rewriting it to say someone else must not work.
    """
    cookie = create_session("uid-123", "owner@example.com", True, SECRET, 30)
    payload = _payload_of(cookie)
    payload["email"] = "attacker@example.com"
    payload["uid"] = "uid-999"
    forged = f"{_b64(json.dumps(payload).encode())}.{cookie.split('.', 1)[1]}"

    assert read_session(forged, SECRET) is None


def test_rejects_an_expiry_pushed_into_the_future():
    """Same attack, aimed at the clock instead of the name.

    An expired session that can be revived by editing one number would
    never really expire.
    """
    cookie = create_session("uid-123", "owner@example.com", True, SECRET, 30)
    payload = _payload_of(cookie)
    payload["exp"] = int(time.time()) + 10 * 365 * 86400
    forged = f"{_b64(json.dumps(payload).encode())}.{cookie.split('.', 1)[1]}"

    assert read_session(forged, SECRET) is None


def test_rejects_a_correctly_signed_but_expired_cookie():
    """Signed by us, honestly, and still no longer valid.

    Negative days puts the expiry in the past, which is what an old
    cookie looks like without waiting a month for one.
    """
    cookie = create_session("uid-123", "owner@example.com", True, SECRET, -1)
    assert read_session(cookie, SECRET) is None


@pytest.mark.parametrize(
    "cookie",
    ["", "no-dot-in-here", ".", "not-base64.not-base64", "a.b.c"],
)
def test_rejects_malformed_cookies_without_raising(cookie):
    """Junk must be refused, not crash the request.

    An unhandled error here would turn a bad cookie into a 500, which
    reads as "JARVIS is broken" rather than "you are not signed in".
    """
    assert read_session(cookie, SECRET) is None


def test_refuses_an_empty_secret_even_for_a_cookie_it_matches():
    """No secret means no valid sessions, not a secret of "".

    If an empty secret were usable, anyone could sign their own cookie
    with the same empty string and be let straight in.
    """
    cookie = create_session("uid-123", "owner@example.com", True, "", 30)
    assert read_session(cookie, "") is None
