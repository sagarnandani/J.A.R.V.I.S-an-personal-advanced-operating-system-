"""Live voice: Gemini speaking directly, not the browser reading text.

The browser's own speech engine reads text aloud. This is different in
kind: the model produces speech itself, so it handles mixed Kannada,
Hindi, Marathi and English the way a person does -- including hearing
them mixed in one sentence, which no dictation-then-text pipeline manages
well.

Shape of it, taken from the owner's own working build (jarvis-core):

    browser  <--websocket-->  JARVIS  <--websocket-->  Gemini Live

JARVIS sits in the middle rather than letting the page talk to Google
directly, for one reason: the API key. A browser cannot hold a secret, so
a page connecting straight to Gemini would have to carry the key, and
anyone opening dev tools would have it. Relaying costs a hop and keeps
the key on the server where it belongs.

Audio is raw PCM both ways -- 16kHz going up, 24kHz coming back -- carried
as base64 inside JSON frames. Text transcripts of both sides come back
alongside the audio, which is what lets a spoken exchange be written into
memory like any other.
"""
import asyncio
import base64
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from google import genai
from google.genai import types

from app import audit, facts, memory
from app.auth import is_owner
from app.config import get_settings
from app.llm.base import system_prompt_with
from app.session import COOKIE_NAME, read_session

logger = logging.getLogger("jarvis.live")

router = APIRouter()

# Gemini's native-audio model and voice. Both are what the owner's other
# JARVIS uses successfully on a free key -- evidence rather than a guess,
# which matters here because a wrong model name fails at connect time with
# a message that explains nothing.
LIVE_MODEL = "gemini-3.1-flash-live-preview"
DEFAULT_VOICE = "Kore"

# Audio formats the Live API expects and returns. Not negotiable, and
# wrong values produce silence rather than an error.
INPUT_RATE = 16000
OUTPUT_RATE = 24000

# A voice conversation is still a conversation, so the model gets the same
# instructions and the same memory as the typed one. Without this, talking
# to JARVIS would reach something that had never heard of you.
_VOICE_NOTE = (
    "\n\nYou are speaking aloud, so keep replies short and natural -- a "
    "sentence or two unless more is genuinely needed. Never read out "
    "punctuation, markdown or lists. Reply in whatever language the owner "
    "speaks, including when they mix languages within a sentence, and use "
    "the same mixture back."
)


def _authorise(websocket: WebSocket, settings) -> dict | None:
    """Who is on the other end, or None.

    Two checks. The session cookie says who they are -- browsers send
    cookies on a same-origin WebSocket handshake, so no separate ticket is
    needed. The Origin header says the connection came from our own page:
    WebSocket handshakes are not covered by CORS, so without this any
    website could open a socket to JARVIS and the browser would attach the
    cookie for them.
    """
    origin = websocket.headers.get("origin")
    host = websocket.headers.get("host")
    if origin and host and origin.split("//")[-1] != host:
        logger.warning("Refusing live socket from foreign origin %s", origin)
        return None

    cookie = websocket.cookies.get(COOKIE_NAME)
    if not cookie:
        return None
    payload = read_session(cookie, settings.session_secret)
    if not payload or not is_owner(payload, settings):
        return None
    return payload


async def _say(ws: WebSocket, **payload) -> None:
    try:
        await ws.send_text(json.dumps(payload))
    except Exception:  # noqa: BLE001 - the socket is gone; nothing to do
        pass


@router.websocket("/v1/live")
async def live_voice(websocket: WebSocket) -> None:
    settings = get_settings()
    await websocket.accept()

    who = _authorise(websocket, settings)
    if not who:
        await _say(websocket, type="error", message="Not signed in as the owner.")
        await websocket.close(code=4401)
        return

    if not settings.gemini_api_key:
        await _say(
            websocket,
            type="error",
            message="Live voice needs GEMINI_API_KEY on the server. "
            "The browser voice still works without it.",
        )
        await websocket.close(code=4400)
        return

    # The same long-term memory the typed conversation gets, so the voice
    # knows what JARVIS knows.
    try:
        known = await facts.recall_facts(
            "", limit=settings.memory_facts_limit,
            max_chars=settings.memory_facts_max_chars,
        )
    except Exception:  # noqa: BLE001 - memory is a nicety here, not a gate
        known = []
    context = "\n".join(f"- {f}" for f in known) or None

    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=system_prompt_with(context) + _VOICE_NOTE,
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=settings.live_voice or DEFAULT_VOICE
                )
            )
        ),
        # Both transcripts: the model's own words, and what it heard. The
        # first is what gets stored as JARVIS's reply, the second as the
        # owner's -- so a spoken exchange is remembered like a typed one.
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )

    client = genai.Client(api_key=settings.gemini_api_key)
    model = settings.live_model or LIVE_MODEL

    try:
        async with client.aio.live.connect(model=model, config=config) as session:
            await _say(
                websocket, type="ready", model=model,
                voice=settings.live_voice or DEFAULT_VOICE,
                input_rate=INPUT_RATE, output_rate=OUTPUT_RATE,
            )
            await _pump(websocket, session, who, settings)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("Live session failed: %s", exc)
        await _say(websocket, type="error", message=_explain(exc, model))
    finally:
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass


def _explain(exc: Exception, model: str) -> str:
    """Say what went wrong in words the owner can act on."""
    text = str(exc)
    low = text.lower()
    if "not found" in low or "404" in text:
        return (
            f"The live voice model '{model}' was refused by Google. Set "
            f"LIVE_MODEL to a current live model in Render → Environment. "
            f"Google said: {text}"
        )
    if "resource_exhausted" in low or "quota" in low or "429" in text:
        return (
            "The free quota for live voice is used up for now. It resets, "
            "and the typed conversation still works. Google said: " + text
        )
    if "api_key" in low or "permission" in low or "401" in text or "403" in text:
        return "The Gemini API key was refused for live voice. Google said: " + text
    return "Live voice could not start. Google said: " + text


async def _pump(websocket: WebSocket, session, who: dict, settings) -> None:
    """Carry audio both ways until one side hangs up."""
    said: list[str] = []   # what the owner said, this turn
    heard: list[str] = []  # what JARVIS replied, this turn

    async def browser_to_gemini() -> None:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            kind = msg.get("type")
            if kind == "audio":
                await session.send_realtime_input(
                    audio=types.Blob(
                        data=base64.b64decode(msg["data"]),
                        mime_type=f"audio/pcm;rate={INPUT_RATE}",
                    )
                )
            elif kind == "text":
                await session.send_client_content(
                    turns=types.Content(
                        role="user", parts=[types.Part(text=msg["text"])]
                    ),
                    turn_complete=True,
                )
            elif kind == "end":
                return

    async def gemini_to_browser() -> None:
        nonlocal said, heard
        async for response in session.receive():
            server = getattr(response, "server_content", None)

            if getattr(response, "data", None):
                await _say(
                    websocket, type="audio",
                    data=base64.b64encode(response.data).decode(),
                )

            if server is None:
                continue

            inp = getattr(server, "input_transcription", None)
            if inp and inp.text:
                said.append(inp.text)
                await _say(websocket, type="you", text=inp.text)

            out = getattr(server, "output_transcription", None)
            if out and out.text:
                heard.append(out.text)
                await _say(websocket, type="jarvis", text=out.text)

            if getattr(server, "interrupted", None):
                await _say(websocket, type="interrupted")

            if getattr(server, "turn_complete", None):
                await _remember("".join(said).strip(), "".join(heard).strip(), who)
                said, heard = [], []
                await _say(websocket, type="turn_complete")

    up = asyncio.create_task(browser_to_gemini())
    down = asyncio.create_task(gemini_to_browser())
    done, pending = await asyncio.wait(
        {up, down}, return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()
    for task in done:
        exc = task.exception()
        if exc and not isinstance(exc, (WebSocketDisconnect, asyncio.CancelledError)):
            raise exc


async def _remember(said: str, replied: str, who: dict) -> None:
    """Write a spoken exchange into memory, exactly like a typed one.

    Without this a voice conversation would vanish the moment it ended,
    and JARVIS would remember only what you typed -- which would make the
    memory it does have quietly wrong about what you have told it.

    Never allowed to break the conversation: the owner is mid-sentence.
    """
    if not said or not replied:
        return
    try:
        await memory.store_exchange(said, replied)
        await audit.log_audit(
            actor=f"user:{who.get('email') or who.get('uid')}",
            action="llm_voice_exchange",
            category="low_risk",
            outcome="success",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not store the spoken exchange: %s", exc)
