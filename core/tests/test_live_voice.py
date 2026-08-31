"""The live-voice relay: who may connect, and what crosses it.

This is the only WebSocket in JARVIS and the only place the Gemini API key
could escape to a browser, so the tests here are mostly about the door
rather than the audio.

Gemini itself is stubbed. What is being checked is the relay: that an
unauthorised socket is refused before any upstream session is opened, that
audio is framed the way the Live API requires, and that a spoken exchange
is written to memory like a typed one.
"""
import asyncio
import base64
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
from app.routes import live as live_route
from app.session import COOKIE_NAME, create_session

OWNER_EMAIL = "sagarnandani99@gmail.com"
SECRET = "test-secret-not-used-anywhere-real"


class FakeSession:
    """Stands in for a Gemini Live session."""

    def __init__(self, script=(), ends_when_done=False):
        self.script = list(script)
        self.sent_audio = []
        self.sent_text = []
        self.turns_served = 0
        # True means this session GOES AWAY once its script runs out,
        # which is what Gemini does after a while. False means it waits
        # for more speech, like a healthy session.
        self.ends_when_done = ends_when_done

    async def send_realtime_input(self, audio=None):
        self.sent_audio.append(audio)

    async def send_client_content(self, turns=None, turn_complete=None):
        self.sent_text.append(turns)

    async def receive(self):
        """Ends at turn_complete, exactly as the real SDK does.

        This is the behaviour that broke continuous conversation: the
        first version of this fake streamed everything from one call and
        then idled, so it could not have caught the bug. A stub that is
        more forgiving than the real thing tests nothing.
        """
        while self.script:
            item = self.script.pop(0)
            yield item
            sc = getattr(item, "server_content", None)
            if sc is not None and getattr(sc, "turn_complete", None):
                self.turns_served += 1
                return
        # Out of script: either the session has gone away, or it is
        # healthy and waiting for the owner to speak again.
        if self.ends_when_done:
            return
        await asyncio.sleep(3600)


class FakeConnect:
    def __init__(self, session, sessions=None):
        self.session = session
        # When given a queue, each connect hands out the next one -- which
        # is how a dropped session and its replacement get scripted.
        self.sessions = list(sessions) if sessions else None
        self.opened = 0
        self.model = None
        self.config = None

    def __call__(self, *, model, config):
        self.model, self.config = model, config
        return self

    async def __aenter__(self):
        self.opened += 1
        if self.sessions:
            self.session = self.sessions.pop(0)
        return self.session

    async def __aexit__(self, *a):
        return False


def _turn(*, audio=None, said=None, replied=None, complete=False):
    server = SimpleNamespace(
        input_transcription=SimpleNamespace(text=said) if said else None,
        output_transcription=SimpleNamespace(text=replied) if replied else None,
        interrupted=None,
        turn_complete=complete,
    )
    return SimpleNamespace(data=audio, server_content=server)


@pytest.fixture
def wired(monkeypatch):
    settings = Settings(
        dev_mode=False, owner_email=OWNER_EMAIL, session_secret=SECRET,
        gemini_api_key="fake-key", memory_facts_enabled=True,
    )
    monkeypatch.setattr(live_route, "get_settings", lambda: settings)
    monkeypatch.setattr(live_route.facts, "recall_facts", _async_return([]))

    class Recorder(list):
        """A list that can also carry what was learned."""
        learned: list = []

    stored = Recorder()

    async def fake_store(said, replied):
        stored.append((said, replied))
        return ("mem-user", "mem-reply")

    monkeypatch.setattr(live_route.memory, "store_exchange", fake_store)
    monkeypatch.setattr(live_route.audit, "log_audit", _async_return(None))

    learned = []

    async def fake_learn(provider, said, replied, source_id, max_facts):
        learned.append((said, replied))
        return (1, 0)

    monkeypatch.setattr(live_route.facts, "learn_from_exchange", fake_learn)
    monkeypatch.setattr(live_route, "_extractor", lambda s: object())
    monkeypatch.setattr(live_route, "get_settings", lambda: settings)
    stored.learned = learned

    def build(script=(), sessions=None):
        session = FakeSession(script)
        connect = FakeConnect(session, sessions)
        monkeypatch.setattr(
            live_route.genai, "Client",
            lambda api_key: SimpleNamespace(aio=SimpleNamespace(live=SimpleNamespace(connect=connect))),
        )
        app = FastAPI()
        app.include_router(live_route.router)
        return TestClient(app, base_url="https://testserver"), session, connect, stored

    return build


def _async_return(value):
    async def _fn(*a, **k):
        return value
    return _fn


def _cookie(client, email=OWNER_EMAIL, uid="uid-1"):
    client.cookies.set(COOKIE_NAME, create_session(uid, email, True, SECRET, 30))
    return client


# --- the door --------------------------------------------------------------

def test_a_socket_without_a_session_is_refused(wired):
    """And refused BEFORE any upstream session is opened.

    Opening a Gemini session for an unauthenticated caller would spend the
    owner's quota on a stranger, even if the socket were closed a moment
    later.
    """
    client, _, connect, _ = wired()
    with client.websocket_connect("/v1/live") as ws:
        msg = ws.receive_json()
    assert msg["type"] == "error"
    assert "signed in" in msg["message"].lower()
    assert connect.model is None, "no upstream session may be opened"


def test_someone_elses_valid_session_is_refused(wired):
    client, _, connect, _ = wired()
    _cookie(client, email="stranger@example.com", uid="uid-9")
    with client.websocket_connect("/v1/live") as ws:
        assert ws.receive_json()["type"] == "error"
    assert connect.model is None


def test_a_socket_from_another_website_is_refused(wired):
    """WebSocket handshakes are not covered by CORS.

    Without an Origin check, any page on the internet could open a socket
    to JARVIS and the browser would attach the owner's cookie to it.
    """
    client, _, connect, _ = wired()
    _cookie(client)
    with client.websocket_connect("/v1/live", headers={"origin": "https://evil.example"}) as ws:
        assert ws.receive_json()["type"] == "error"
    assert connect.model is None


def test_a_missing_api_key_is_explained_not_crashed(wired, monkeypatch):
    client, _, connect, _ = wired()
    monkeypatch.setattr(
        live_route, "get_settings",
        lambda: Settings(owner_email=OWNER_EMAIL, session_secret=SECRET, gemini_api_key=None),
    )
    _cookie(client)
    with client.websocket_connect("/v1/live") as ws:
        msg = ws.receive_json()
    assert msg["type"] == "error"
    assert "GEMINI_API_KEY" in msg["message"]
    assert "browser voice still works" in msg["message"]


# --- what crosses the relay -----------------------------------------------

def test_the_owner_gets_a_session_with_audio_and_both_transcripts(wired):
    """Audio out, and text for both sides.

    The transcripts are not decoration: they are what lets a spoken
    exchange be shown on screen and written to memory.
    """
    client, _, connect, _ = wired()
    _cookie(client)
    with client.websocket_connect("/v1/live") as ws:
        ready = ws.receive_json()

    assert ready["type"] == "ready"
    assert ready["input_rate"] == 16000 and ready["output_rate"] == 24000
    cfg = connect.config
    assert cfg.response_modalities == ["AUDIO"]
    assert cfg.speech_config.voice_config.prebuilt_voice_config.voice_name == "Kore"
    assert cfg.input_audio_transcription is not None
    assert cfg.output_audio_transcription is not None


def test_the_voice_is_told_to_follow_the_owners_language(wired):
    """Including a mixture of languages inside one sentence.

    This is the instruction that makes Kannada-in-English work rather
    than the model politely answering in English.
    """
    client, _, connect, _ = wired()
    _cookie(client)
    with client.websocket_connect("/v1/live") as ws:
        ws.receive_json()
    instruction = connect.config.system_instruction.lower()
    assert "mix languages" in instruction
    assert "speaking aloud" in instruction


def test_microphone_audio_reaches_gemini_as_16k_pcm(wired):
    """The rate is not negotiable and a wrong one fails silently."""
    client, session, _, _ = wired()
    _cookie(client)
    raw = b"\x01\x02" * 64
    with client.websocket_connect("/v1/live") as ws:
        ws.receive_json()
        ws.send_text(json.dumps({"type": "audio", "data": base64.b64encode(raw).decode()}))
        ws.send_text(json.dumps({"type": "end"}))

    assert session.sent_audio, "nothing reached Gemini"
    blob = session.sent_audio[0]
    assert blob.data == raw, "audio must arrive as raw bytes, not base64"
    assert blob.mime_type == "audio/pcm;rate=16000"


def test_gemini_audio_and_transcripts_reach_the_browser(wired):
    client, _, _, _ = wired([
        _turn(said="ನಮಸ್ಕಾರ "),
        _turn(audio=b"\x10\x20" * 8),
        _turn(replied="ನಮಸ್ಕಾರ! "),
        _turn(complete=True),
    ])
    _cookie(client)
    seen = []
    with client.websocket_connect("/v1/live") as ws:
        ws.receive_json()
        # you, audio, jarvis, turn_complete -- four, not five. Asking for
        # one more than the script produces blocks forever rather than
        # failing, which is why the timeout below exists.
        for _ in range(4):
            seen.append(ws.receive_json())

    kinds = [m["type"] for m in seen]
    assert "you" in kinds and "jarvis" in kinds and "audio" in kinds
    audio = next(m for m in seen if m["type"] == "audio")
    assert base64.b64decode(audio["data"]) == b"\x10\x20" * 8


def test_a_spoken_exchange_is_remembered_like_a_typed_one(wired):
    """Otherwise a voice conversation vanishes when it ends.

    Worse than losing it: the memory JARVIS does keep would then be
    quietly wrong about what it has been told.
    """
    client, _, _, stored = wired([
        _turn(said="my favourite colour is red"),
        _turn(replied="Noted."),
        _turn(complete=True),
    ])
    _cookie(client)
    with client.websocket_connect("/v1/live") as ws:
        ws.receive_json()
        for _ in range(3):
            ws.receive_json()

    assert stored == [("my favourite colour is red", "Noted.")]


def test_a_half_exchange_is_not_stored(wired):
    """Storing a question with no answer would leave recall lopsided."""
    client, _, _, stored = wired([_turn(said="hello"), _turn(complete=True)])
    _cookie(client)
    with client.websocket_connect("/v1/live") as ws:
        ws.receive_json()
        for _ in range(2):
            ws.receive_json()
    assert stored == []


# --- staying open ----------------------------------------------------------

def test_the_conversation_continues_without_pressing_the_button_again(wired):
    """Two exchanges on one session, which is what "continuous" means.

    The SDK's receive() ends the moment a turn completes. Iterating it
    once meant the relay finished after the first reply and closed the
    whole session, so every exchange needed the microphone pressed again.
    A fresh iterator per turn keeps the one Gemini session open.
    """
    client, session, _, stored = wired([
        _turn(said="what is my colour?"),
        _turn(replied="Red."),
        _turn(complete=True),
        _turn(said="and my name?"),
        _turn(replied="Sagar."),
        _turn(complete=True),
    ])
    _cookie(client)

    kinds = []
    with client.websocket_connect("/v1/live") as ws:
        ws.receive_json()  # ready
        for _ in range(6):  # you, jarvis, turn_complete -- twice
            kinds.append(ws.receive_json()["type"])

    assert kinds.count("turn_complete") == 2, "the session closed after one turn"
    assert session.turns_served == 2, "receive() must be called again per turn"
    assert stored == [
        ("what is my colour?", "Red."),
        ("and my name?", "Sagar."),
    ], "both exchanges must be remembered"


def test_the_session_survives_a_turn_that_stores_nothing(wired):
    """A turn with no transcript must not end the conversation.

    Background noise can complete a turn with nothing said. Treating that
    as the end of the session would drop the owner mid-conversation for
    coughing.
    """
    client, session, _, stored = wired([
        _turn(complete=True),
        _turn(said="still there?"),
        _turn(replied="Yes."),
        _turn(complete=True),
    ])
    _cookie(client)

    with client.websocket_connect("/v1/live") as ws:
        ws.receive_json()
        seen = [ws.receive_json()["type"] for _ in range(4)]

    assert seen.count("turn_complete") == 2
    assert stored == [("still there?", "Yes.")]


def test_a_dropped_session_is_picked_back_up(wired, monkeypatch):
    """Gemini ends a live session on its own after a while.

    Without this, a long conversation dies mid-sentence and looks like
    JARVIS losing interest rather than a connection expiring.
    """
    monkeypatch.setattr(live_route, "_backoff", lambda attempt: 0)

    dropped = FakeSession(
        [_turn(said="first"), _turn(replied="one"), _turn(complete=True)],
        ends_when_done=True,
    )
    resumed = FakeSession([_turn(said="second"), _turn(replied="two"), _turn(complete=True)])

    client, _, connect, stored = wired(sessions=[dropped, resumed])
    _cookie(client)

    kinds = []
    with client.websocket_connect("/v1/live") as ws:
        kinds.append(ws.receive_json()["type"])          # ready
        for _ in range(3):
            kinds.append(ws.receive_json()["type"])      # first exchange
        kinds.append(ws.receive_json()["type"])          # reconnecting
        kinds.append(ws.receive_json()["type"])          # resumed
        for _ in range(3):
            kinds.append(ws.receive_json()["type"])      # second exchange

    assert kinds[0] == "ready"
    assert "reconnecting" in kinds
    assert "resumed" in kinds
    assert connect.opened == 2, "a second session must be opened"
    assert stored == [("first", "one"), ("second", "two")], (
        "both exchanges must survive the reconnect"
    )


def test_it_stops_trying_and_says_so_rather_than_hammering(wired, monkeypatch):
    """A revoked key or exhausted quota must not become an infinite loop.

    Reconnecting forever would hit the API hard and never tell the owner
    why JARVIS went quiet.
    """
    monkeypatch.setattr(live_route, "_backoff", lambda attempt: 0)
    monkeypatch.setattr(live_route, "MAX_RECONNECTS", 2)

    # Sessions that open and immediately go away, carrying nothing --
    # what an exhausted quota looks like mid-stream.
    client, _, connect, _ = wired(
        sessions=[FakeSession([], ends_when_done=True) for _ in range(8)]
    )
    _cookie(client)

    with client.websocket_connect("/v1/live") as ws:
        msgs = []
        for _ in range(8):
            msgs.append(ws.receive_json())
            if msgs[-1]["type"] == "error":
                break

    assert msgs[-1]["type"] == "error"
    assert "kept dropping" in msgs[-1]["message"]
    assert connect.opened <= 4, "must give up rather than reconnect forever"


# --- the two gaps that made spoken memory useless -------------------------

def test_speech_becomes_a_permanent_fact(wired):
    """Saying something aloud must be able to become permanent.

    It could not before: the spoken exchange was stored as conversation
    and never looked at again, so telling JARVIS your wife's name out loud
    was forgotten by the next day. Typing the same sentence worked. That
    difference is invisible until it bites.
    """
    client, _, _, stored = wired([
        _turn(said="my wife is called Sneha"),
        _turn(replied="Noted, sir."),
        _turn(complete=True),
    ])
    _cookie(client)
    with client.websocket_connect("/v1/live") as ws:
        ws.receive_json()
        for _ in range(3):
            ws.receive_json()

    assert stored.learned == [("my wife is called Sneha", "Noted, sir.")], (
        "a spoken exchange must reach the fact extractor"
    )


def test_the_spoken_session_is_given_the_recent_conversation(wired, monkeypatch):
    """Voice had facts but not the conversation -- half a memory.

    Speaking to JARVIS reached something that had read your permanent
    facts and had no idea what you said to it two minutes ago.
    """
    from app.llm.base import ASSISTANT, USER, Turn

    monkeypatch.setattr(
        live_route.memory, "recall_turns",
        _async_return([Turn(USER, "my colour is red"), Turn(ASSISTANT, "Noted.")]),
    )
    monkeypatch.setattr(live_route.facts, "recall_facts", _async_return(["Owner is an engineer"]))
    monkeypatch.setattr(live_route.status, "briefing", _async_return("Exchanges today: 4"))

    client, _, connect, _ = wired()
    _cookie(client)
    with client.websocket_connect("/v1/live") as ws:
        ws.receive_json()

    instruction = connect.config.system_instruction
    assert "Owner is an engineer" in instruction, "facts missing"
    assert "my colour is red" in instruction, "recent conversation missing"
    assert "Exchanges today: 4" in instruction, "status missing"


def test_a_broken_extractor_never_interrupts_the_conversation(wired, monkeypatch):
    """The owner is mid-sentence. Bookkeeping must not stop them."""
    async def explode(*a, **k):
        raise RuntimeError("extractor down")

    monkeypatch.setattr(live_route.facts, "learn_from_exchange", explode)

    client, _, _, stored = wired([
        _turn(said="hello"), _turn(replied="Welcome back, sir."), _turn(complete=True),
    ])
    _cookie(client)
    with client.websocket_connect("/v1/live") as ws:
        ws.receive_json()
        kinds = [ws.receive_json()["type"] for _ in range(3)]

    assert "turn_complete" in kinds
    assert stored == [("hello", "Welcome back, sir.")]
