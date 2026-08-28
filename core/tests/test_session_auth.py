"""The cookie must actually get you through the door -- and only you.

`test_session.py` proves the cookie itself cannot be forged. This proves
the request handling honours it: that a signed-in browser stays signed in
across reloads (the bug being fixed), and that a valid cookie belonging to
somebody else still gets nowhere.

These go through the real FastAPI app so the wiring is what is tested. A
unit test of `read_session` alone would have passed happily while the
cookie was never read on a request.
"""
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.auth import get_current_user
from app.config import Settings, get_settings
from app.session import COOKIE_NAME, create_session

OWNER_EMAIL = "sagarnandani99@gmail.com"
OWNER_UID = "firebase-uid-abc123"
SECRET = "test-secret-not-used-anywhere-real"


@pytest.fixture
def client(monkeypatch):
    """A minimal app with one protected endpoint.

    Deliberately not the real `app`: importing that would pull in the
    database lifespan and the static files mount, neither of which has
    anything to do with what is being tested here.
    """
    settings = Settings(
        dev_mode=False,
        owner_uid=OWNER_UID,
        owner_email=OWNER_EMAIL,
        session_secret=SECRET,
        session_days=30,
    )
    monkeypatch.setattr("app.auth.get_settings", lambda: settings)

    app = FastAPI()

    @app.get("/protected")
    async def protected(user=Depends(get_current_user)):
        return {"uid": user.uid, "email": user.email}

    return TestClient(app)


def with_cookie(client: TestClient, cookie: str) -> TestClient:
    client.cookies.set(COOKIE_NAME, cookie)
    return client


def test_no_cookie_and_no_header_is_not_signed_in(client):
    res = client.get("/protected")
    assert res.status_code == 401
    # The message has to tell a non-technical owner what to do about it.
    assert "sign-in" in res.json()["detail"].lower()


def test_a_valid_cookie_signs_you_in(client):
    """The fix, stated as a test.

    No Authorization header anywhere -- just the cookie a browser would
    send back on its own after a reload.
    """
    cookie = create_session(OWNER_UID, OWNER_EMAIL, True, SECRET, 30)
    res = with_cookie(client, cookie).get("/protected")
    assert res.status_code == 200
    assert res.json() == {"uid": OWNER_UID, "email": OWNER_EMAIL}


def test_a_tampered_cookie_does_not_sign_you_in(client):
    cookie = create_session(OWNER_UID, OWNER_EMAIL, True, SECRET, 30)
    payload, signature = cookie.split(".", 1)
    res = with_cookie(client, f"{payload}x.{signature}").get("/protected")
    assert res.status_code == 401


def test_an_expired_cookie_does_not_sign_you_in(client):
    cookie = create_session(OWNER_UID, OWNER_EMAIL, True, SECRET, -1)
    res = with_cookie(client, cookie).get("/protected")
    assert res.status_code == 401


def test_a_genuine_cookie_for_a_different_person_is_refused(client):
    """Ownership is re-checked per request, not trusted from issue time.

    Nothing forged here: this cookie is validly signed by this server. It
    simply names somebody who is not the owner -- which is what an old
    cookie looks like after OWNER_EMAIL is changed to lock someone out.
    Refusing it is the point: otherwise that lockout would not take effect
    until the old session expired on its own.
    """
    cookie = create_session("uid-999", "someone.else@example.com", True, SECRET, 30)
    res = with_cookie(client, cookie).get("/protected")
    assert res.status_code == 403


def test_an_unverified_email_in_a_cookie_is_refused(client):
    """`email_verified` has to survive the round trip into the cookie.

    Ownership by email is only safe because the provider confirmed the
    address. If the cookie dropped that flag, a session issued to a
    uid-matched owner would be fine, but the email rule would silently
    stop applying -- so it is checked explicitly.
    """
    cookie = create_session("uid-999", OWNER_EMAIL, False, SECRET, 30)
    res = with_cookie(client, cookie).get("/protected")
    assert res.status_code == 403


# --- the whole flow, end to end ---

@pytest.fixture
def login_client(monkeypatch):
    """The real sign-in endpoints, with only Google's part stubbed.

    Verifying a Google token needs Google. Everything after that -- the
    owner check, issuing the cookie, and reading it back on the next
    request -- is ours, and is what this exercises.
    """
    from app.routes import login as login_route

    settings = Settings(
        dev_mode=False,
        owner_uid=None,
        owner_email=OWNER_EMAIL,
        session_secret=SECRET,
        session_days=30,
    )
    monkeypatch.setattr("app.auth.get_settings", lambda: settings)
    monkeypatch.setattr(login_route, "get_settings", lambda: settings)

    def fake_verify(token: str, _settings):
        return {
            "uid": f"uid-for-{token}",
            "email": OWNER_EMAIL if token == "owners-token" else "nobody@example.com",
            "email_verified": True,
        }

    monkeypatch.setattr(login_route, "verify_token", fake_verify)

    app = FastAPI()
    app.include_router(login_route.router)
    # https, because the cookie is issued with Secure set and a client that
    # honours that -- as every browser does -- will not send it back over
    # plain http. A test served over http would silently never carry the
    # cookie, and would be testing nothing.
    return TestClient(app, base_url="https://testserver")


def test_signing_in_then_reloading_stays_signed_in(login_client):
    """The bug, as a test.

    The second request carries no token at all -- only the cookie the
    browser kept. That is exactly what a page reload looks like, and it is
    what used to come back as "Not signed in yet."
    """
    res = login_client.post("/auth/session", json={"credential": "owners-token"})
    assert res.status_code == 200
    assert COOKIE_NAME in res.cookies

    me = login_client.get("/v1/me")
    assert me.status_code == 200
    assert me.json()["email"] == OWNER_EMAIL


def test_the_cookie_is_not_readable_by_javascript(login_client):
    """A stolen session is worse than a failed sign-in.

    HttpOnly is what stops a scripting flaw on the page from reading the
    cookie and using it elsewhere. It is one keyword, easy to drop in a
    refactor and invisible when it goes missing, so it is asserted.
    """
    res = login_client.post("/auth/session", json={"credential": "owners-token"})
    set_cookie = res.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie


def test_someone_elses_google_account_is_refused_a_session(login_client):
    res = login_client.post("/auth/session", json={"credential": "strangers-token"})
    assert res.status_code == 403
    assert COOKIE_NAME not in res.cookies
    # The refusal has to name who it thinks you are, or it is unactionable.
    assert "nobody@example.com" in res.json()["detail"]


def test_signing_out_clears_the_session(login_client):
    login_client.post("/auth/session", json={"credential": "owners-token"})
    assert login_client.get("/v1/me").status_code == 200

    login_client.post("/auth/logout")
    assert login_client.get("/v1/me").status_code == 401
