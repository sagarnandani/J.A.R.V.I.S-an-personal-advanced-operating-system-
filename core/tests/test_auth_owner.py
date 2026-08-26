"""Who gets let in.

Stage 0 is single-user by design, so this rule is the entire access
control layer. Tested directly rather than only through a live Firebase
login, so the failure modes that matter are pinned down.
"""
from app.auth import is_owner
from app.config import Settings

OWNER_EMAIL = "sagarnandani99@gmail.com"
OWNER_UID = "firebase-uid-abc123"


def token(**overrides) -> dict:
    base = {
        "uid": OWNER_UID,
        "email": OWNER_EMAIL,
        "email_verified": True,
    }
    base.update(overrides)
    return base


# --- matching by UID ---

def test_matching_uid_is_owner():
    s = Settings(owner_uid=OWNER_UID, owner_email=None)
    assert is_owner(token(), s) is True


def test_different_uid_is_not_owner():
    s = Settings(owner_uid=OWNER_UID, owner_email=None)
    assert is_owner(token(uid="somebody-else"), s) is False


# --- matching by email ---

def test_matching_verified_email_is_owner():
    s = Settings(owner_uid=None, owner_email=OWNER_EMAIL)
    assert is_owner(token(uid="any-uid"), s) is True


def test_email_match_is_case_insensitive():
    s = Settings(owner_uid=None, owner_email="Sagarnandani99@Gmail.com")
    assert is_owner(token(), s) is True


def test_unverified_email_is_rejected_even_if_it_matches():
    """The important one: otherwise anyone could register an unverified
    account claiming the owner's address and walk straight in."""
    s = Settings(owner_uid=None, owner_email=OWNER_EMAIL)
    assert is_owner(token(email_verified=False), s) is False


def test_missing_email_claim_is_rejected():
    s = Settings(owner_uid=None, owner_email=OWNER_EMAIL)
    assert is_owner(token(email=None), s) is False


def test_different_email_is_not_owner():
    s = Settings(owner_uid=None, owner_email=OWNER_EMAIL)
    assert is_owner(token(email="stranger@example.com"), s) is False


# --- both / neither configured ---

def test_either_identifier_matching_is_enough():
    s = Settings(owner_uid=OWNER_UID, owner_email=OWNER_EMAIL)
    assert is_owner(token(email="stranger@example.com"), s) is True   # uid matches
    assert is_owner(token(uid="other-uid"), s) is True                # email matches


def test_neither_configured_admits_nobody():
    """The API returns a 500 in this case rather than relying on this
    alone, but the rule itself must still refuse."""
    s = Settings(owner_uid=None, owner_email=None)
    assert is_owner(token(), s) is False
