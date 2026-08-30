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

    def __init__(self, script=()):
        self.script = list(script)
        self.sent_audio = []
        self.sent_text = []

    async def send_realtime_input(self, audio=None):
        self.sent_audio.append(audio)

    async def send_client_content(self, turns=None, turn_complete=None):
        self.sent_text.append(turns)

    async def receive(self):
        for item in self.script:
            yield item
        # Then idle, the way a real session waits for more speech.
        await asyncio.sleep(3600)


class FakeConnect:
    def __init__(self, session):
        self.session = session
        self.model = None
        self.config = None

    def __call__(self, *, model, config):
        self.model, self.config = model, config
        return self

    async def __aenter__(self):
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
        gemini_api_key="fake-key", memory_facts_enabled=False,
    )
    monkeypatch.setattr(live_route, "get_settings", lambda: settings)
    monkeypatch.setattr(live_route.facts, "recall_facts", _async_return([]))

    stored = []

    async def fake_store(said, replied):
        stored.append((said, replied))
        return ("a", "b")

    monkeypatch.setattr(live_route.memory, "store_exchange", fake_store)
    monkeypatch.setattr(live_route.audit, "log_audit", _async_return(None))

    def build(script=()):
        session = FakeSession(script)
        connect = FakeConnect(session)
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
