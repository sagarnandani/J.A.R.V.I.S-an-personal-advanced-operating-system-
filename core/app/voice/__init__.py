"""Voice as a module of its own, replaceable without touching JARVIS.

Importing this package registers the engines. Nothing above it names a
provider: callers ask `engine.say(...)` and get whichever engine this
deployment has, with the browser as the fallback that always works.
"""
from app.voice import browser as _browser  # noqa: F401  (registers itself)
from app.voice.engine import Spoken, VoiceEngine, chunks, known, say, select
from app.voice.profile import (
    DELIVERIES,
    JARVIS_VOICE_V1,
    VoiceProfile,
    delivery,
)

__all__ = [
    "DELIVERIES", "JARVIS_VOICE_V1", "Spoken", "VoiceEngine", "VoiceProfile",
    "chunks", "delivery", "known", "say", "select",
]
