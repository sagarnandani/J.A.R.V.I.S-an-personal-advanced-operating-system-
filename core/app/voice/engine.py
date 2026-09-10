"""The seam a text-to-speech provider plugs into.

One rule: nothing outside this package knows which engine is speaking.
The rest of JARVIS asks for a voice and gets one, the same way it asks
for a model and gets one, and for the same reason -- providers get
retired, repriced and outgrown, and the day that happens should cost one
new file rather than a search through the codebase.

Two engines exist today. `browser` is what has always run: the device's
own speech synthesiser, which needs no key, no server CPU and no money,
and which cannot sound like the brief asks. A server-side neural engine
is the upgrade, and it is a new module implementing `VoiceEngine` plus one
setting -- deliberately not built here, because an engine that has never
produced a sound on the hardware it will run on is a guess with a class
name.

Engines say what they can do rather than being assumed to do it.
`streams` is the one that matters: an engine that can begin speaking
before the sentence is finished is the difference between conversation
and dictation, and pretending an engine streams when it does not makes
the pipeline above it wrong.
"""
import logging
from dataclasses import dataclass
from typing import Protocol

from app.voice import profile as vp

logger = logging.getLogger("jarvis.voice")


@dataclass(frozen=True)
class Spoken:
    """One piece of speech, ready to be produced.

    `audio` is None for an engine that does not synthesise here -- the
    browser engine hands back settings and the client's own synthesiser
    makes the sound. That is a real engine, not a placeholder: it is what
    speaks today, and modelling it as one keeps the caller from having
    two shapes to handle.
    """

    text: str
    engine: str
    delivery: str
    settings: dict
    audio: bytes | None = None
    mime: str = ""


class VoiceEngine(Protocol):
    """What every provider must offer."""

    name: str
    streams: bool          # can begin speaking before the text is complete
    synthesises: bool      # produces audio here, rather than on the client

    def available(self, settings) -> bool:
        """Can this engine actually run on this deployment right now?"""

    def configure(self, voice: vp.VoiceProfile, delivery: vp.Delivery) -> dict:
        """The profile, translated into this provider's own settings."""

    async def say(self, text: str, voice: vp.VoiceProfile,
                  delivery: vp.Delivery) -> Spoken:
        """Produce the speech, or the instructions to produce it."""


_ENGINES: dict[str, VoiceEngine] = {}


def register(engine: VoiceEngine) -> None:
    _ENGINES[engine.name] = engine


def known() -> list[str]:
    return sorted(_ENGINES)


def select(settings) -> VoiceEngine:
    """The engine that will speak, and the fallback if it cannot.

    Configured first, then whatever is available, then the browser --
    which is last on purpose, because it is the one that always works.
    An owner whose neural engine has fallen over should hear a plainer
    JARVIS, not silence, and should be able to see which one spoke.
    """
    wanted = (getattr(settings, "voice_engine", "") or "").strip().lower()
    if wanted and wanted in _ENGINES and _ENGINES[wanted].available(settings):
        return _ENGINES[wanted]

    if wanted and wanted in _ENGINES:
        logger.warning("Voice engine %r is configured but not available; "
                       "falling back.", wanted)
    elif wanted:
        logger.warning("Voice engine %r is not registered; falling back.", wanted)

    for name, engine in sorted(_ENGINES.items()):
        if name != "browser" and engine.available(settings):
            return engine
    return _ENGINES["browser"]


async def say(text: str, settings, delivery: str | None = None,
              voice: vp.VoiceProfile | None = None) -> Spoken:
    """Speak one piece of text in JARVIS's voice."""
    engine = select(settings)
    return await engine.say(
        text, voice or vp.JARVIS_VOICE_V1, vp.delivery(delivery)
    )


# --- turning a reply into things that can be said one at a time ------------

# Sentence ends, without breaking on the dots inside "Rs.1,200" or "e.g."
# A wrong split is heard immediately: the voice stops mid-thought and
# starts again, which sounds like a fault rather than a pause.
_ABBREVIATIONS = ("mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st",
                  "rs", "no", "vs", "etc", "e.g", "i.e", "a.m", "p.m")


def sentences(text: str) -> list[str]:
    """Split prose into sentences without breaking Rs.1,200 or e.g.

    A wrong split is heard immediately: the voice stops mid-thought and
    starts again, which sounds like a fault rather than a pause.
    """
    out: list[str] = []
    buf = ""
    n = len(text)
    for i, ch in enumerate(text):
        buf += ch
        if ch in ".!?…":
            word = buf.rstrip(".!?… ").rsplit(" ", 1)[-1].lower()
            after = text[i + 1] if i + 1 < n else " "
            if after in " \n\t" or i + 1 >= n:
                if word not in _ABBREVIATIONS and not word.isdigit():
                    out.append(buf.strip())
                    buf = ""
        elif ch == "\n" and buf.strip():
            out.append(buf.strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return [s for s in out if s]


def chunks(text: str, min_chars: int = 20, max_chars: int = 240) -> list[str]:
    """Split a reply into pieces that can be spoken one at a time.

    This is the whole of the latency story on an engine that cannot
    stream. Speaking a four-sentence reply as one utterance means waiting
    for all of it to be prepared; handing over the first sentence on its
    own means JARVIS starts talking while the rest is still queued.

    Two corrections applied afterwards. A fragment too short to stand on
    its own is joined to the sentence after it, because a voice that stops
    after three words sounds broken. A sentence too long to start quickly
    is cut at a comma, never mid-word.
    """
    if not text or not text.strip():
        return []

    parts = sentences(text)

    merged: list[str] = []
    for part in parts:
        if merged and len(merged[-1]) < min_chars:
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)
    # A trailing fragment has nothing after it to join, so it joins back.
    if len(merged) > 1 and len(merged[-1]) < min_chars:
        merged[-2] = f"{merged[-2]} {merged.pop()}"

    pieces: list[str] = []
    for part in merged:
        while len(part) > max_chars:
            cut = part.rfind(",", 0, max_chars)
            if cut < min_chars:
                cut = part.rfind(" ", 0, max_chars)
            if cut < min_chars:
                break
            pieces.append(part[: cut + 1].strip())
            part = part[cut + 1 :].strip()
        if part:
            pieces.append(part)
    return pieces
