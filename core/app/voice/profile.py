"""JARVIS_VOICE_V1: every voice setting, in one place.

Before this file the voice was decided in four places at once -- a model
name and a preset in config, a regular expression matching voice names in
the browser, and a hard-coded rate and pitch a few lines below it. Changing
how JARVIS sounds meant finding all four and hoping there was not a fifth.

So the profile is the single source, and it is written in the owner's terms
-- British, forty, medium-low, unhurried -- rather than in any one
provider's. Each engine translates it into whatever knobs it actually has.
That translation living in the engine is what makes a provider swap a new
file rather than an edit to everything.

Deliberately not a description of a person. The brief was explicit and it
is also the only defensible position: this is an original voice with a set
of qualities, not an impression of an actor.
"""
from dataclasses import dataclass, field

NAME = "JARVIS_VOICE_V1"


@dataclass(frozen=True)
class Delivery:
    """How a particular kind of line should be said.

    The same voice, said differently. A completion report and a warning
    delivered identically is the flatness that makes an assistant sound
    like a machine reading text, and it is a cheap thing to fix: the
    words are already different, only the pace and weight need to follow.

    `rate` and `pitch` are multipliers against the profile's own settings,
    never absolutes, so retuning the base voice carries through all six
    without touching them.
    """

    key: str
    what: str
    rate: float = 1.0
    pitch: float = 1.0


# The six the brief names. Ordinary conversation is the default and
# everything else is a small departure from it -- large departures are how
# a controlled voice starts acting.
DELIVERIES: dict[str, Delivery] = {
    d.key: d for d in (
        Delivery("normal", "Relaxed, intelligent, concise."),
        Delivery("analysis", "Slower, focused, deliberate.", rate=0.94),
        Delivery("important", "Authoritative, with subtle emphasis.",
                 rate=0.96, pitch=0.97),
        Delivery("warning", "Firm, controlled, direct. Never raised.",
                 rate=0.95, pitch=0.95),
        Delivery("success", "Calm confidence, slight warmth.",
                 rate=1.0, pitch=1.02),
        Delivery("humour", "Understated. The timing does the work.",
                 rate=0.97, pitch=1.0),
    )
}

DEFAULT_DELIVERY = "normal"


@dataclass(frozen=True)
class VoiceProfile:
    """What JARVIS sounds like, independent of what produces the sound."""

    name: str = NAME
    accent: str = "British"
    gender: str = "male"
    apparent_age: int = 40
    pitch: str = "medium-low"

    # Words per minute, which is the one pacing number that means the same
    # thing to every engine. Each engine converts it into its own units,
    # and the conversion is a guess until measured -- see `browser.py`.
    words_per_minute: int = 140

    warmth: str = "subtle"
    confidence: str = "high"
    emotional_range: str = "controlled"
    dry_wit: str = "subtle"
    narration_style: bool = False
    robotic_effect: bool = False

    # What it must not sound like. Kept as data because the engines that
    # take a written description -- voice design, and any future prompt-
    # driven provider -- need it, and because it is the half of the brief
    # most easily lost.
    avoid: tuple[str, ...] = (
        "movie-trailer delivery",
        "radio announcer",
        "audiobook narration",
        "excessively deep",
        "monotone",
        "overacting",
        "artificial pauses between words",
        "exaggerated British mannerisms",
        "robotic or metallic effects",
        "exaggerated emotion",
    )

    # Preferred voices per engine, best first. Names rather than ids
    # because the browser exposes names, and an engine that uses ids maps
    # these itself.
    prefer: dict[str, tuple[str, ...]] = field(default_factory=lambda: {
        # iOS and macOS ship Daniel; Arthur is the newer en-GB male.
        # Neither will sound cinematic, and that is a limit of the device
        # rather than of this configuration.
        "browser": ("Arthur", "Daniel", "Oliver", "George", "Google UK English Male"),
    })

    def described(self) -> str:
        """The profile as a sentence, for an engine that designs from text."""
        return (
            f"A {self.apparent_age}-year-old {self.accent} {self.gender} voice. "
            f"{self.pitch.capitalize()} pitch, around {self.words_per_minute} "
            f"words per minute. Calm, precise and articulate, effortlessly "
            f"confident, conversational rather than narrated. {self.warmth.capitalize()} "
            f"warmth with {self.emotional_range} emotion and {self.dry_wit} dry wit. "
            f"Close-microphone studio character, minimal breathiness, human "
            f"rather than synthetic. Never: " + ", ".join(self.avoid) + "."
        )

    def as_dict(self) -> dict:
        return {
            "name": self.name, "accent": self.accent, "gender": self.gender,
            "apparent_age": self.apparent_age, "pitch": self.pitch,
            "words_per_minute": self.words_per_minute, "warmth": self.warmth,
            "confidence": self.confidence, "emotional_range": self.emotional_range,
            "dry_wit": self.dry_wit, "narration_style": self.narration_style,
            "robotic_effect": self.robotic_effect, "avoid": list(self.avoid),
            "described": self.described(),
        }


JARVIS_VOICE_V1 = VoiceProfile()


def delivery(key: str | None) -> Delivery:
    """A delivery by name, falling back to ordinary conversation.

    Falls back rather than raising: a caller asking for a mode that does
    not exist should get JARVIS speaking normally, not silence.
    """
    return DELIVERIES.get((key or "").lower().strip(), DELIVERIES[DEFAULT_DELIVERY])
