"""The Firebase sign-in proxy.

Sign-in only completes if the browser sees one domain throughout, so the
pieces that make that true -- where requests are forwarded, and cookies
being re-scoped to our host -- are pinned here.
"""
from app.routes.auth_proxy import (
    forwardable_request_headers,
    rewrite_set_cookie,
    upstream_url,
)

PROJECT = "jarvis-by-claude-a1026"


# --- where requests go ---------------------------------------------------

def test_forwards_to_the_projects_firebase_host():
    assert upstream_url(PROJECT, "handler", "") == (
        f"https://{PROJECT}.firebaseapp.com/__/auth/handler"
    )


def test_query_string_is_preserved():
    """Google's sign-in state travels in the query string. Dropping it
    breaks the round trip in a way that looks like a silent failure."""
    url = upstream_url(PROJECT, "handler", "apiKey=abc&providerId=google.com")
    assert url.endswith("/__/auth/handler?apiKey=abc&providerId=google.com")


def test_nested_paths_are_kept():
    assert upstream_url(PROJECT, "iframe.js", "") .endswith("/__/auth/iframe.js")


# --- cookies -------------------------------------------------------------

def test_domain_is_stripped_so_the_cookie_applies_to_us():
    """The reason this proxy exists. A cookie scoped to firebaseapp.com is
    rejected by a browser talking to our host; without the Domain
    attribute it binds to whoever served it, which is us."""
    out = rewrite_set_cookie("session=xyz; Domain=jarvis.firebaseapp.com; Path=/; Secure")
    assert "Domain=" not in out
    assert "session=xyz" in out


def test_other_cookie_attributes_survive():
    out = rewrite_set_cookie("a=b; Domain=x.firebaseapp.com; HttpOnly; Secure; SameSite=None")
    for keep in ("HttpOnly", "Secure", "SameSite=None"):
        assert keep in out


def test_cookie_without_a_domain_is_left_alone():
    assert rewrite_set_cookie("a=b; Path=/") == "a=b; Path=/"


# --- headers -------------------------------------------------------------

def test_host_header_is_not_forwarded():
    """Forwarding our own Host would send Firebase a name it doesn't
    serve, and the request would be refused."""
    out = forwardable_request_headers({"host": "jarvis.onrender.com", "accept": "*/*"})
    assert "host" not in {k.lower() for k in out}
    assert out["accept"] == "*/*"


def test_connection_level_headers_are_dropped():
    out = forwardable_request_headers(
        {"content-length": "12", "transfer-encoding": "chunked", "cookie": "a=b"}
    )
    assert set(out) == {"cookie"}
