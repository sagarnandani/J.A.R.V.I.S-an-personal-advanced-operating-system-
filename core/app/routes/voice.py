"""What JARVIS sounds like, served to whatever is going to speak.

One endpoint, and it exists so the voice is configured in one place. The
page used to decide for itself: its own list of acceptable voice names,
its own rate, its own pitch, none of it visible from the server and all of
it needing a deploy to change. Now the profile lives on the server and the
page asks.

It also means the page does not need to know which engine is speaking. It
is told, along with whether that engine makes the sound itself.
"""
import logging

from fastapi import APIRouter, Depends

from app import voice
from app.auth import CurrentUser, get_current_user
from app.config import get_settings

router = APIRouter()
logger = logging.getLogger("jarvis.routes.voice")


@router.get("/v1/voice", include_in_schema=False)
async def voice_profile(
    delivery: str = "normal",
    user: CurrentUser = Depends(get_current_user),
    settings=Depends(get_settings),
) -> dict:
    """The active engine and JARVIS's voice, in that engine's own terms."""
    engine = voice.select(settings)
    profile = voice.JARVIS_VOICE_V1
    mode = voice.delivery(delivery)

    return {
        "engine": {
            "name": engine.name,
            "streams": engine.streams,
            # False means the client synthesises. The page needs to know,
            # because it changes what it does with the answer.
            "synthesises_on_server": engine.synthesises,
            "available": voice.known(),
        },
        "profile": profile.as_dict(),
        "settings": engine.configure(profile, mode),
        "deliveries": {
            key: {
                "what": d.what,
                "settings": engine.configure(profile, d),
            }
            for key, d in voice.DELIVERIES.items()
        },
        # The live conversation is a different path and cannot take this
        # profile, so the page is told rather than left to assume.
        "live": {
            "model": settings.live_model,
            "voice": settings.live_voice,
            "takes_profile": False,
            "why": "Gemini Live produces audio from the model directly. "
                   "There is no synthesis step to configure, so its voice "
                   "is one of Gemini's presets.",
        },
    }


@router.post("/v1/voice/lines", include_in_schema=False)
async def split_lines(
    body: dict,
    user: CurrentUser = Depends(get_current_user),
    settings=Depends(get_settings),
) -> dict:
    """A reply, split into pieces that can be spoken one at a time.

    Server-side so that every client splits the same way, and so that a
    future engine which synthesises here can use the identical boundaries
    -- two different splitters would mean the voice changed character the
    day the engine did.
    """
    text = str(body.get("text") or "")
    mode = voice.delivery(body.get("delivery"))
    engine = voice.select(settings)
    return {
        "lines": voice.chunks(text),
        "delivery": mode.key,
        "settings": engine.configure(voice.JARVIS_VOICE_V1, mode),
    }
