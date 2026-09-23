"""A model running on the owner's own hardware (sections 13 and 14).

On a Rs.3,500 ceiling the difference between free and a fraction of a
paisa per call is the difference between JARVIS noticing things all day
and JARVIS rationing itself. So the cheap tier goes to his own machine
when he has one.

These run against a real HTTP server standing in for Ollama, because the
thing most likely to be wrong is the shape of the request and the
handling of a server that is switched off -- and a mocked client would
agree with whatever I wrote.

The rule worth stating: the local model is chosen, never fallen back to.
A 7B model on a home server is a real model with real limits, and
answering a hard question with it because a frontier model was busy is
the silent substitution this whole layer exists to prevent.
"""
import json
import socket
import threading
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.agents import model_router
from app.agents.schemas import ModelTier
from app.budget import estimate_cost_inr, estimate_shadow_inr, provider_rates
from app.config import Settings
from app.llm import LOCAL, REAL, get_provider
from app.llm.local_adapter import LocalAdapter, LocalUnavailable, reachable

MODEL = "llama3.2"


class _Handler(BaseHTTPRequestHandler):
    """Enough of an OpenAI-compatible server to be worth testing against."""

    behaviour = "ok"
    seen: dict = {}

    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.endswith("/models"):
            if _Handler.behaviour == "no_model":
                return self._send(200, {"data": [{"id": "something-else"}]})
            if _Handler.behaviour == "not_a_model_server":
                raw = b"<html>hello</html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            return self._send(200, {"data": [{"id": MODEL}]})
        self._send(404, {"error": "no"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        _Handler.seen = {
            "path": self.path,
            "body": json.loads(self.rfile.read(length) or b"{}"),
            "auth": self.headers.get("Authorization"),
        }
        if _Handler.behaviour == "no_model":
            return self._send(404, {"error": {"message": f"model '{MODEL}' not found"}})
        if _Handler.behaviour == "broken":
            return self._send(500, {"error": {"message": "out of memory"}})
        if _Handler.behaviour == "nonsense":
            return self._send(200, {"not": "what an OpenAI api returns"})
        self._send(200, {
            "choices": [{"message": {"role": "assistant",
                                     "content": "The answer, locally."}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 40},
        })


@pytest.fixture(scope="module")
def server():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()


@pytest.fixture(autouse=True)
def behaving():
    _Handler.behaviour = "ok"
    _Handler.seen = {}
    yield
    _Handler.behaviour = "ok"


def configured(url, **kw) -> Settings:
    return Settings(llm_provider=kw.pop("llm_provider", "gemini"),
                    local_llm_url=url, local_llm_model=MODEL,
                    model_cheap="flash-lite", gemini_model="flash",
                    model_deep="pro", **kw)


# --- talking to it ---------------------------------------------------------

@pytest.mark.asyncio
async def test_it_answers(server):
    result = await LocalAdapter(server, MODEL).complete("What is the deadline?")
    assert result.text == "The answer, locally."
    assert result.provider == "local"
    assert result.model == MODEL
    assert result.input_tokens == 120 and result.output_tokens == 40


@pytest.mark.asyncio
async def test_it_speaks_the_shape_every_local_server_speaks(server):
    """Not an endorsement of OpenAI -- it is what Ollama, llama.cpp,
    LM Studio and vLLM all already speak."""
    await LocalAdapter(server, MODEL).complete("hello")
    assert _Handler.seen["path"] == "/v1/chat/completions"
    body = _Handler.seen["body"]
    assert body["model"] == MODEL
    assert body["stream"] is False, "a streaming response would not parse"
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][-1] == {"role": "user", "content": "hello"}


@pytest.mark.asyncio
async def test_the_url_works_with_or_without_the_version_suffix(server):
    for url in (server, server + "/", server + "/v1", server + "/v1/"):
        result = await LocalAdapter(url, MODEL).complete("hello")
        assert result.text == "The answer, locally."
        # The exact path, not the end of it. `endswith` is satisfied by
        # /v1/v1/chat/completions, which is what a version suffix added
        # to a URL that already had one actually produces.
        assert _Handler.seen["path"] == "/v1/chat/completions", (
            f"{url} became {_Handler.seen['path']}")


@pytest.mark.asyncio
async def test_no_key_is_sent_when_there_is_none(server):
    """Most local servers want none, so sending an empty one would be a
    header that means something it does not."""
    await LocalAdapter(server, MODEL).complete("hello")
    assert _Handler.seen["auth"] is None

    await LocalAdapter(server, MODEL, api_key="secret").complete("hello")
    assert _Handler.seen["auth"] == "Bearer secret"


@pytest.mark.asyncio
async def test_the_conversation_is_carried(server):
    from app.llm.base import Turn

    await LocalAdapter(server, MODEL).complete(
        "and then?", history=[Turn(role="user", text="hello"),
                              Turn(role="assistant", text="hi")])
    roles = [m["role"] for m in _Handler.seen["body"]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]


# --- when it is not there --------------------------------------------------

@pytest.mark.asyncio
async def test_a_server_that_is_not_running_says_what_to_start():
    with pytest.raises(LocalUnavailable) as raised:
        await LocalAdapter("http://127.0.0.1:1", MODEL).complete("hello")
    assert "ollama serve" in str(raised.value).lower()
    assert "LOCAL_LLM_URL" in str(raised.value)


@pytest.mark.asyncio
async def test_a_missing_model_says_how_to_pull_it(server):
    _Handler.behaviour = "no_model"
    with pytest.raises(LocalUnavailable) as raised:
        await LocalAdapter(server, MODEL).complete("hello")
    assert f"ollama pull {MODEL}" in str(raised.value)


@pytest.mark.asyncio
async def test_a_server_that_errors_says_what_it_said(server):
    _Handler.behaviour = "broken"
    with pytest.raises(LocalUnavailable, match="out of memory"):
        await LocalAdapter(server, MODEL).complete("hello")


@pytest.mark.asyncio
async def test_something_that_is_not_a_model_server_says_so(server):
    _Handler.behaviour = "nonsense"
    with pytest.raises(LocalUnavailable, match="not in the shape"):
        await LocalAdapter(server, MODEL).complete("hello")


# --- is it there at all ----------------------------------------------------

@pytest.mark.asyncio
async def test_reachable_says_yes_when_it_is(server):
    up, said = await reachable(server, MODEL)
    assert up is True and MODEL in said


@pytest.mark.asyncio
async def test_reachable_never_raises_whatever_is_wrong():
    """It decides whether the cheap tier goes local, so it is asked often
    and a raise here would take out the router."""
    for url in ("", "http://127.0.0.1:1", "not-a-url", "http://nope.invalid"):
        up, said = await reachable(url, MODEL)
        assert up is False and said


@pytest.mark.asyncio
async def test_reachable_is_honest_about_a_model_it_does_not_have(server):
    """A wrong yes sends every cheap task to something that is not there."""
    _Handler.behaviour = "no_model"
    up, said = await reachable(server, MODEL)
    assert up is False
    assert "does not have" in said and "something-else" in said


@pytest.mark.asyncio
async def test_a_web_server_that_is_not_a_model_server_is_not_reachable(server):
    _Handler.behaviour = "not_a_model_server"
    up, _ = await reachable(server, MODEL)
    assert up is False


# --- where the router sends things ----------------------------------------

def test_cheap_work_goes_to_the_local_model(server):
    choice = model_router.choose(configured(server), tier=ModelTier.CHEAP)
    assert choice.provider == "local"
    assert choice.model == MODEL
    assert "your own machine" in choice.reason and "free" in choice.reason


@pytest.mark.parametrize("tier", [ModelTier.STANDARD, ModelTier.DEEP])
def test_real_work_never_goes_to_the_local_model(server, tier):
    """The rule that makes this safe.

    A 7B model on a home server is a real model with real limits.
    Answering a hard question with it to save money is the silent
    substitution the provider layer exists to prevent.
    """
    choice = model_router.choose(configured(server), tier=tier)
    assert choice.provider != "local", (
        f"{tier.value} work was routed to the local model")


def test_with_no_local_model_nothing_changes(server):
    plain = Settings(llm_provider="gemini", model_cheap="flash-lite",
                     gemini_model="flash", model_deep="pro")
    choice = model_router.choose(plain, tier=ModelTier.CHEAP)
    assert choice.provider == "gemini" and choice.model == "flash-lite"


def test_the_local_model_is_last_in_the_fallback_order():
    """Chosen on purpose, never fallen back into."""
    assert REAL[-1] == LOCAL


# --- money -----------------------------------------------------------------

def test_a_local_call_costs_nothing():
    settings = Settings()
    assert provider_rates("local", settings) == (Decimal("0"), Decimal("0"))
    assert estimate_cost_inr(100_000, 50_000, settings, provider="local") == 0


def test_but_what_it_saved_is_still_counted():
    """Zero shadow as well would make an afternoon of local work look
    like an afternoon of nothing -- and "what this would have cost" is
    the number that justifies running it."""
    settings = Settings()
    saved = estimate_shadow_inr(100_000, 50_000, settings, provider="local")
    assert saved > 0, "local work showed as having saved nothing"


def test_what_it_saved_is_never_counted_as_money():
    settings = Settings()
    spent = estimate_cost_inr(100_000, 50_000, settings, provider="local")
    saved = estimate_shadow_inr(100_000, 50_000, settings, provider="local")
    assert spent == 0 and saved > 0


# --- built, and reachable through the ordinary door ------------------------

def test_asking_for_it_by_name_builds_it(server):
    provider = get_provider(configured(server, llm_provider="local"))
    assert isinstance(provider, LocalAdapter)


def test_a_url_is_what_configures_it_not_a_key(server):
    """Most local servers want no key, so requiring one would mean the
    feature never switched on for anybody."""
    assert isinstance(get_provider(configured(server, llm_provider="local")),
                      LocalAdapter)
    none_set = Settings(llm_provider="local")
    assert not isinstance(get_provider(none_set), LocalAdapter)


def test_it_is_no_longer_reported_as_not_built():
    from app.llm import NOT_BUILT

    assert "local" not in NOT_BUILT
