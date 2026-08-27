"""Firebase token verification, tested with real signed tokens.

We generate a throwaway signing key, sign tokens with it, and hand the
matching public certificate to the verifier. That exercises the real
signature path -- no mocking of the crypto -- so the rejections below are
genuine rejections, not stubbed ones.

This is the check standing between the whole system and anyone who finds
the URL, so "it looked right" isn't good enough.
"""
import datetime
import json

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import HTTPException
from google.auth import crypt as google_crypt
from google.auth import jwt as google_jwt

from app import auth
from app.config import Settings

PROJECT_ID = "jarvis-by-claude-a1026"
KID = "test-key-1"


@pytest.fixture(scope="module")
def signing():
    """A throwaway RSA key plus the self-signed certificate for it."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "jarvis-test")]
    )
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return {"private_pem": private_pem, "certs": {KID: pem}}


def make_token(signing, **claim_overrides) -> str:
    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    claims = {
        "iss": f"https://securetoken.google.com/{PROJECT_ID}",
        "aud": PROJECT_ID,
        "sub": "firebase-uid-abc123",
        "email": "sagarnandani99@gmail.com",
        "email_verified": True,
        "iat": now - 10,
        "exp": now + 3600,
    }
    claims.update(claim_overrides)
    return google_jwt.encode(
        google_crypt.RSASigner.from_string(signing["private_pem"], KID), claims
    ).decode()


@pytest.fixture(autouse=True)
def use_test_certs(signing, monkeypatch):
    """Serve our test certificate instead of fetching Google's."""
    monkeypatch.setattr(
        auth,
        "_get_certs",
        # Same fake certificate for both key sets -- the test signs every
        # token with one key, and which URL was consulted is not what these
        # tests are pinning.
        lambda url=auth.FIREBASE_CERTS_URL, force_refresh=False: signing["certs"],
    )


def settings(**overrides) -> Settings:
    base = dict(firebase_project_id=PROJECT_ID, owner_email="sagarnandani99@gmail.com")
    base.update(overrides)
    return Settings(**base)


# --- accepted -------------------------------------------------------------

def test_valid_token_is_accepted(signing):
    claims = auth.verify_firebase_token(make_token(signing), settings())
    assert claims["uid"] == "firebase-uid-abc123"
    assert claims["email"] == "sagarnandani99@gmail.com"


# --- rejected -------------------------------------------------------------

def test_token_for_another_firebase_project_is_rejected(signing):
    """The one that matters most: a perfectly valid token from somebody
    else's Firebase project must not open our door."""
    token = make_token(
        signing,
        aud="someone-elses-project",
        iss="https://securetoken.google.com/someone-elses-project",
    )
    with pytest.raises(HTTPException) as exc:
        auth.verify_firebase_token(token, settings())
    assert exc.value.status_code == 401


def test_wrong_issuer_is_rejected(signing):
    """Right audience, wrong issuer -- the library does not check this, so
    our own check has to."""
    token = make_token(signing, iss="https://evil.example.com/")
    with pytest.raises(HTTPException) as exc:
        auth.verify_firebase_token(token, settings())
    assert exc.value.status_code == 401
    assert "issuer" in exc.value.detail.lower()


def test_expired_token_is_rejected(signing):
    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    token = make_token(signing, iat=now - 7200, exp=now - 3600)
    with pytest.raises(HTTPException) as exc:
        auth.verify_firebase_token(token, settings())
    assert exc.value.status_code == 401


def test_token_signed_by_the_wrong_key_is_rejected(signing):
    """Someone forging a token with their own key gets nowhere."""
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_pem = other_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    forged = google_jwt.encode(
        google_crypt.RSASigner.from_string(other_pem, KID),
        {
            "iss": f"https://securetoken.google.com/{PROJECT_ID}",
            "aud": PROJECT_ID,
            "sub": "attacker",
            "iat": now - 10,
            "exp": now + 3600,
        },
    ).decode()
    with pytest.raises(HTTPException) as exc:
        auth.verify_firebase_token(forged, settings())
    assert exc.value.status_code == 401


def test_garbage_token_is_rejected():
    with pytest.raises(HTTPException) as exc:
        auth.verify_firebase_token("not-a-jwt-at-all", settings())
    assert exc.value.status_code == 401


def test_missing_project_id_is_a_server_error(signing):
    with pytest.raises(HTTPException) as exc:
        auth.verify_firebase_token(make_token(signing), settings(firebase_project_id=""))
    assert exc.value.status_code == 500


# --- Google's in-page sign-in button ------------------------------------
#
# A second, separate kind of token. It has to be verified against Google's
# other key set, with our OAuth client ID as the audience -- and it must be
# possible to accept it without weakening any of the checks above.

GOOGLE_CLIENT_ID = "701562415519-example.apps.googleusercontent.com"


def google_token(signing, **overrides) -> str:
    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    claims = {
        "iss": "https://accounts.google.com",
        "aud": GOOGLE_CLIENT_ID,
        "sub": "google-user-1",
        "email": "sagarnandani99@gmail.com",
        "email_verified": True,
        "iat": now - 10,
        "exp": now + 3600,
    }
    claims.update(overrides)
    return google_jwt.encode(
        google_crypt.RSASigner.from_string(signing["private_pem"], KID), claims
    ).decode()


def google_settings(**overrides) -> Settings:
    base = dict(
        firebase_project_id=PROJECT_ID,
        google_client_id=GOOGLE_CLIENT_ID,
        owner_email="sagarnandani99@gmail.com",
    )
    base.update(overrides)
    return Settings(**base)


def test_google_button_token_is_accepted(signing):
    claims = auth.verify_token(google_token(signing), google_settings())
    assert claims["uid"] == "google-user-1"
    assert claims["email"] == "sagarnandani99@gmail.com"


def test_google_token_for_another_app_is_rejected(signing):
    """A token Google minted for somebody else's app must not work here."""
    token = google_token(signing, aud="some-other-app.apps.googleusercontent.com")
    with pytest.raises(HTTPException) as exc:
        auth.verify_token(token, google_settings())
    assert exc.value.status_code == 401


def test_google_token_with_forged_issuer_is_rejected(signing):
    token = google_token(signing, iss="https://accounts.evil.example")
    with pytest.raises(HTTPException) as exc:
        auth.verify_token(token, google_settings())
    assert exc.value.status_code == 401


def test_expired_google_token_is_rejected(signing):
    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    token = google_token(signing, iat=now - 7200, exp=now - 3600)
    with pytest.raises(HTTPException) as exc:
        auth.verify_token(token, google_settings())
    assert exc.value.status_code == 401


def test_firebase_token_still_works_through_the_shared_entry_point(signing):
    """Adding the second route must not break the first."""
    claims = auth.verify_token(make_token(signing), google_settings())
    assert claims["uid"] == "firebase-uid-abc123"


def test_a_token_from_neither_source_is_refused(signing):
    token = google_token(signing, iss="https://login.microsoftonline.com/x")
    with pytest.raises(HTTPException) as exc:
        auth.verify_token(token, google_settings())
    assert "not issued by a sign-in method this server accepts" in exc.value.detail
