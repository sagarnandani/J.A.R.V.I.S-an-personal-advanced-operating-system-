"""Posting to LinkedIn, and the gate in front of it.

The media chain researched, decided, wrote, fact-checked, reviewed and
waited for the owner's yes -- and then stopped. Every gate was built and
the last step was missing.

Adding that step means adding the first thing JARVIS can do that the
world sees and that cannot be taken back. So most of this file is about
the gate rather than the posting:

- it cannot post without the owner's yes, by default;
- it posts what was REVIEWED, not what a model has in hand now;
- turning off the asking is one deliberate act by him, in one place, and
  is not something an agent can arrange for itself;
- and "I posted it" is never taken on trust: the link comes back or the
  answer says it did not.
"""
import json
import socket
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio

from app.agents import permissions, registry, runtime, tasks
from app.agents.capabilities import publish
from app.agents.schemas import (
    ALWAYS_APPROVED,
    AgentError,
    AgentSpec,
    ApprovalRequired,
    Handoff,
    Lifecycle,
    ModelTier,
    Permission,
)
from app.social import linkedin
from app.social.linkedin import NotConnected, PostFailed

SETTINGS = SimpleNamespace(linkedin_client_id="id", linkedin_client_secret="sh",
                           linkedin_api_version="202405")


@pytest_asyncio.fixture
async def clean(db_pool):
    await db_pool.execute(
        "DELETE FROM social_accounts; DELETE FROM social_oauth_states; "
        "DELETE FROM content_pieces; DELETE FROM task_approvals; "
        "DELETE FROM tasks; DELETE FROM workflows; DELETE FROM agents; "
        "DELETE FROM audit_log;")
    # The shipped policy, restored on the way IN as well as out.
    #
    # A test here switches publishing to 'auto' to prove that switch
    # exists, and left it that way -- so the very next run of the test
    # that proves nothing posts without asking found publishing already
    # automatic, posted, and passed for the wrong reason before failing.
    # A test that mutates a safety policy and does not put it back is
    # worse than no test: it disarms the thing it is checking.
    await db_pool.execute(
        "UPDATE approvals SET default_policy = 'ask_every_time' "
        "WHERE action_category IN "
        "('publishing','spending','credentials_or_security')")
    registry._IMPLEMENTATIONS.clear()
    yield db_pool
    registry._IMPLEMENTATIONS.clear()
    await db_pool.execute(
        "DELETE FROM social_accounts; DELETE FROM social_oauth_states; "
        "DELETE FROM content_pieces;")
    await db_pool.execute(
        "UPDATE approvals SET default_policy = 'ask_every_time' "
        "WHERE action_category IN "
        "('publishing','spending','credentials_or_security')")


async def connected(pool, *, scopes=("openid", "profile", "w_member_social"),
                    expires_in_days=60):
    expires = (datetime.now(timezone.utc) + timedelta(days=expires_in_days)
               if expires_in_days is not None else None)
    await pool.execute(
        "INSERT INTO social_accounts (platform, account_urn, account_name, "
        "access_token, expires_at, scopes) "
        "VALUES ('linkedin','urn:li:person:abc','Sagar','tok',$1,$2) "
        "ON CONFLICT (platform) DO UPDATE SET scopes = EXCLUDED.scopes, "
        "expires_at = EXCLUDED.expires_at",
        expires, list(scopes))


def serving(handler):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            handler(self)

    server = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}", server


# --- the gate --------------------------------------------------------------

def test_posting_is_an_approval_category_not_a_free_action():
    """The runtime stops on PUBLISH. If it ever did not, every other
    check in the media chain would be decoration."""
    assert ALWAYS_APPROVED[Permission.PUBLISH] == "publishing"
    assert Permission.PUBLISH in publish.SPEC.permissions


def test_the_publisher_holds_nothing_else_it_could_do_harm_with():
    held = publish.SPEC.permissions
    assert held == frozenset({Permission.PUBLISH, Permission.READ_MEMORY})
    for never in (Permission.RUN_COMMAND, Permission.WRITE_FILES,
                  Permission.NETWORK, Permission.DELETE, Permission.SPEND,
                  Permission.MODIFY_CONFIG, Permission.MODIFY_AGENTS):
        assert never not in held


@pytest.mark.asyncio
async def test_a_task_that_would_post_stops_and_asks(clean):
    """End to end through the real runtime: nothing goes out unasked."""
    await connected(clean)
    spec = AgentSpec(capability="media.publish", name="Publish it",
                     task_types=("publish",),
                     permissions=frozenset({Permission.PUBLISH,
                                            Permission.READ_MEMORY}),
                     model_tiers=(ModelTier.CHEAP,), status=Lifecycle.ACTIVE)
    await registry.register(spec)

    posted = []

    async def never(*a, **kw):
        posted.append(a)
        return {"platform": "linkedin", "id": "x", "url": "u", "characters": 1}

    registry.implement("media.publish", publish.run)
    original, linkedin.post = linkedin.post, never
    try:
        wf = await tasks.create_workflow("Post it", "user:owner")
        task_id = await tasks.create(
            objective="Post the update", capability="media.publish",
            workflow_id=wf, constraints={"permissions": ["publish"],
                                         "text": "Hello LinkedIn."})
        assert await runtime.run_task(task_id) is False
    finally:
        linkedin.post = original

    assert not posted, "it posted without being asked"
    row = await tasks.get(task_id)
    assert row["status"] == "waiting_approval"
    assert row["awaiting_category"] == "publishing"


@pytest.mark.asyncio
async def test_asking_can_be_switched_off_only_by_the_owner_and_in_one_place(clean):
    """Section 20 allows an explicitly configured policy. This is what
    makes it explicit: one row, set by him, not reachable by an agent."""
    assert await permissions.policy_for("publishing") == "ask_every_time"

    await clean.execute(
        "INSERT INTO approvals (action_category, default_policy) "
        "VALUES ('publishing','auto') ON CONFLICT (action_category) "
        "DO UPDATE SET default_policy = 'auto'")
    assert await permissions.policy_for("publishing") == "auto"

    # And an agent cannot reach it: changing what may happen unasked is
    # MODIFY_CONFIG, which no agent is ever delegated.
    from app.agents.schemas import NEVER_DELEGATED

    assert Permission.MODIFY_CONFIG in NEVER_DELEGATED


# --- what actually gets posted --------------------------------------------

@pytest.mark.asyncio
async def test_it_posts_the_approved_text_not_whatever_it_was_handed(clean):
    """A capability that took free text from another agent would let
    anything that can start a task put words on his profile."""
    await connected(clean)
    piece = await clean.fetchval(
        "INSERT INTO content_pieces (brand, topic, state, package) "
        "VALUES ('me','EV policy','approved',$1) RETURNING id",
        {"post": "The reviewed and approved words."})

    handoff = Handoff(task_id=uuid4(), workflow_id=uuid4(),
                      objective="post it", inputs={"piece_id": str(piece)},
                      context="", constraints={}, expected_output="",
                      budget_inr=None,
                      permissions=frozenset({Permission.PUBLISH}))
    text, from_piece = await publish._text_for(handoff)
    assert text == "The reviewed and approved words."
    assert from_piece == str(piece)


@pytest.mark.asyncio
async def test_a_piece_he_has_not_approved_is_not_posted(clean):
    await connected(clean)
    for state in ("producing", "ready", "rejected", "discarded", "needs_you"):
        piece = await clean.fetchval(
            "INSERT INTO content_pieces (brand, topic, state, package) "
            "VALUES ('me','x',$1,$2) RETURNING id",
            state, {"post": "not yours to post"})
        handoff = Handoff(task_id=uuid4(), workflow_id=uuid4(), objective="x",
                          inputs={"piece_id": str(piece)}, context="",
                          constraints={}, expected_output="", budget_inr=None,
                          permissions=frozenset({Permission.PUBLISH}))
        with pytest.raises(AgentError, match="not approved"):
            await publish._text_for(handoff)


@pytest.mark.asyncio
async def test_a_piece_with_an_unreadable_package_says_so(clean):
    """Rather than an AttributeError, which tells him nothing and looks
    like JARVIS breaking."""
    await connected(clean)
    piece = await clean.fetchval(
        "INSERT INTO content_pieces (brand, topic, state, package) "
        "VALUES ('me','x','approved',$1) RETURNING id", "just a string")
    handoff = Handoff(task_id=uuid4(), workflow_id=uuid4(), objective="x",
                      inputs={"piece_id": str(piece)}, context="",
                      constraints={}, expected_output="", budget_inr=None,
                      permissions=frozenset({Permission.PUBLISH}))
    with pytest.raises(AgentError, match="no readable text"):
        await publish._text_for(handoff)


@pytest.mark.asyncio
async def test_nothing_to_post_is_refused_rather_than_guessed(clean):
    for inputs in ({}, {"text": ""}, {"text": "   "}):
        handoff = Handoff(task_id=uuid4(), workflow_id=uuid4(), objective="x",
                          inputs=inputs, context="", constraints={},
                          expected_output="", budget_inr=None,
                          permissions=frozenset({Permission.PUBLISH}))
        with pytest.raises(AgentError, match="Nothing to publish"):
            await publish._text_for(handoff)


# --- talking to LinkedIn ---------------------------------------------------

@pytest.mark.asyncio
async def test_with_no_account_connected_it_says_how_to_connect(clean):
    with pytest.raises(NotConnected, match="Connect one"):
        await linkedin.post("hello", SETTINGS)


@pytest.mark.asyncio
async def test_an_account_that_never_granted_posting_says_which_product(clean):
    """The commonest way this fails: the app exists, the sign-in worked,
    and LinkedIn never granted w_member_social."""
    await connected(clean, scopes=("openid", "profile"))
    with pytest.raises(NotConnected, match="Share on LinkedIn"):
        await linkedin.post("hello", SETTINGS)


@pytest.mark.asyncio
async def test_an_expired_connection_says_so_rather_than_failing_oddly(clean):
    await connected(clean, expires_in_days=-1)
    with pytest.raises(NotConnected, match="expired"):
        await linkedin.post("hello", SETTINGS)


@pytest.mark.asyncio
async def test_a_post_too_long_says_by_how_much(clean):
    await connected(clean)
    with pytest.raises(PostFailed, match="Shorten it by"):
        await linkedin.post("x" * (linkedin.MAX_LENGTH + 250), SETTINGS)


@pytest.mark.asyncio
async def test_an_empty_post_is_refused(clean):
    await connected(clean)
    for nothing in ("", "   ", "\n"):
        with pytest.raises(PostFailed, match="nothing to post"):
            await linkedin.post(nothing, SETTINGS)


@pytest.mark.asyncio
async def test_a_successful_post_comes_back_with_a_link(clean, monkeypatch):
    """Evidence, not a claim. Section 33: "I posted it" is exactly the
    sentence that must never be taken on trust."""
    await connected(clean)

    def handler(h):
        h.send_response(201)
        h.send_header("x-restli-id", "urn:li:share:7123")
        h.send_header("Content-Type", "application/json")
        h.end_headers()
        h.wfile.write(b"{}")

    base, server = serving(handler)
    monkeypatch.setattr(linkedin, "POSTS", f"{base}/rest/posts")
    try:
        got = await linkedin.post("Hello LinkedIn.", SETTINGS)
    finally:
        server.shutdown()

    assert got["id"] == "urn:li:share:7123"
    assert got["url"].endswith("urn:li:share:7123")
    assert got["characters"] == len("Hello LinkedIn.")


@pytest.mark.asyncio
async def test_a_refusal_carries_linkedins_own_words(clean, monkeypatch):
    """Its errors are the useful part: "unpermitted fields", "not enough
    permissions" and a dead token look identical from outside and need
    completely different fixes."""
    def handler(h):
        h.send_response(403)
        h.send_header("Content-Type", "application/json")
        h.end_headers()
        h.wfile.write(json.dumps(
            {"message": "Not enough permissions to access: POST"}).encode())

    await connected(clean)
    base, server = serving(handler)
    monkeypatch.setattr(linkedin, "POSTS", f"{base}/rest/posts")
    monkeypatch.setattr(linkedin, "UGC_POSTS", f"{base}/v2/ugcPosts")
    try:
        with pytest.raises(PostFailed, match="Not enough permissions"):
            await linkedin.post("hello", SETTINGS)
    finally:
        server.shutdown()


@pytest.mark.asyncio
async def test_it_falls_back_to_the_older_endpoint(clean, monkeypatch):
    """Which posts API an app may use depends on the products LinkedIn
    granted it, which is not knowable from here."""
    await connected(clean)
    seen = []

    def handler(h):
        seen.append(h.path)
        if "rest/posts" in h.path:
            h.send_response(400)
            h.send_header("Content-Type", "application/json")
            h.end_headers()
            h.wfile.write(b'{"message":"unpermitted fields"}')
            return
        h.send_response(201)
        h.send_header("Content-Type", "application/json")
        h.end_headers()
        h.wfile.write(b'{"id":"urn:li:ugcPost:99"}')

    base, server = serving(handler)
    monkeypatch.setattr(linkedin, "POSTS", f"{base}/rest/posts")
    monkeypatch.setattr(linkedin, "UGC_POSTS", f"{base}/v2/ugcPosts")
    try:
        got = await linkedin.post("hello", SETTINGS)
    finally:
        server.shutdown()

    assert [p for p in seen if "ugcPosts" in p], "it never tried the older one"
    assert got["id"] == "urn:li:ugcPost:99"


# --- connecting ------------------------------------------------------------

@pytest.mark.asyncio
async def test_connecting_needs_an_app_configured_first(clean):
    with pytest.raises(NotConnected, match="developers.linkedin.com"):
        await linkedin.start("https://x/cb",
                             SimpleNamespace(linkedin_client_id=""),
                             by="user:owner")


@pytest.mark.asyncio
async def test_the_consent_url_asks_for_posting(clean):
    url = await linkedin.start("https://x/cb", SETTINGS, by="user:owner")
    assert "w_member_social" in url
    assert "response_type=code" in url and "state=" in url


@pytest.mark.asyncio
async def test_a_callback_jarvis_did_not_start_is_refused(clean):
    """Without this, anything that could reach the callback could hand
    JARVIS a token for an account nobody asked to connect."""
    for made_up in ("", "guessed", "x" * 30):
        with pytest.raises(NotConnected, match="did not come from JARVIS"):
            await linkedin.finish("code", made_up, "https://x/cb", SETTINGS,
                                  by="user:owner")


@pytest.mark.asyncio
async def test_a_state_works_once(clean):
    url = await linkedin.start("https://x/cb", SETTINGS, by="user:owner")
    state = url.split("state=")[1].split("&")[0]
    # Used once by a failed exchange; a second attempt must not be allowed
    # to reuse it even though nothing was connected.
    with pytest.raises(Exception):
        await linkedin.finish("bad", state, "https://x/cb", SETTINGS,
                              by="user:owner")
    with pytest.raises(NotConnected, match="did not come from JARVIS"):
        await linkedin.finish("bad", state, "https://x/cb", SETTINGS,
                              by="user:owner")


@pytest.mark.asyncio
async def test_the_token_never_leaves_through_a_route(clean):
    """It cannot be hashed -- an API call replays it -- so the mitigation
    is that nothing returns it."""
    await connected(clean)
    said = await linkedin.state(SETTINGS)
    assert "tok" not in json.dumps(said, default=str)

    account = await linkedin.account()
    assert "tok" not in json.dumps(account.as_detail(), default=str)


@pytest.mark.asyncio
async def test_disconnecting_forgets_the_account(clean):
    await connected(clean)
    assert await linkedin.disconnect() is True
    assert await linkedin.account() is None
    with pytest.raises(NotConnected):
        await linkedin.post("hello", SETTINGS)


def test_what_it_posts_as_is_not_jarvis_s_to_change():
    """Posting is the first thing it does that the world sees and that
    cannot be taken back."""
    from app.constitution import is_protected

    for path, expect in (
        ("core/app/social/linkedin.py", "posts as"),
        ("core/app/agents/capabilities/publish.py", "before something is published"),
    ):
        why = is_protected(path)
        assert why, f"{path} can be rewritten by self-development"
        assert expect in why
