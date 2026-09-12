"""Which intelligence does the work, when the owner has said.

Three modes, and the difference between the first two is the whole point.

**Hard.** "Use Claude only." JARVIS uses that provider or tells him it
cannot. It never substitutes quietly, because a silent substitution turns
a deliberate choice into a suggestion the system feels free to ignore --
and he would have no way of knowing it happened.

**Soft.** "Prefer Claude." Try it, fall back if it cannot answer, and say
which one did.

**None.** The router decides.

Parsed with patterns rather than a model call. A preference that costs a
model call to detect costs one on every message, most of which contain no
preference at all, and a regular expression that mis-reads "I was reading
about Claude today" as an instruction is a bug with an obvious fix --
where a model that does the same is a bug with none.

What a preference is NOT: authority. Choosing who does the work never
changes what the work is allowed to do. Permissions come from the agent's
registry entry and the runtime, and a test holds that line.
"""
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger("jarvis.llm.preference")

HARD, SOFT, NONE = "hard", "soft", "none"

# Every name the owner might use, mapped to the provider id this codebase
# knows. OpenAI is here deliberately even though no adapter exists: being
# asked for it must produce "that is not configured here", not silence.
NAMES: dict[str, tuple[str, ...]] = {
    "claude": ("claude", "anthropic", "sonnet", "opus", "haiku"),
    "gemini": ("gemini", "google", "bard"),
    "openai": ("openai", "chatgpt", "gpt", "gpt-4", "gpt-5", "gpt5", "o1", "o3"),
    "local": ("local model", "local llm", "ollama", "llama", "nano"),
}

# Wording that makes a choice binding. "Only" and "must" are the owner
# ruling something out; "prefer" and "try" are him expressing an order of
# preference.
_BINDING = (
    "only", "must use", "must be", "strictly", "do not use any other",
    "don't use any other", "nothing else", "no other model", "no fallback",
    "do not fall back", "don't fall back", "specifically",
)
_PREFERRING = (
    "prefer", "preferably", "if available", "if possible", "try",
    "first choice", "ideally", "where possible", "if you can",
)

# The instruction has to be an instruction. "Use Claude", "with Claude",
# "via Gemini" -- not "Claude said" or "I read about Gemini".
_ASKED = re.compile(
    r"\b(?:use|using|with|via|through|run\s+(?:it|this|that)\s+(?:on|through)|"
    r"ask|route\s+(?:it|this)\s+to|prefer|preferably|try|switch\s+to|"
    r"stick\s+to|go\s+(?:with|through))\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Preference:
    """What the owner asked for, and how firmly."""

    provider: str | None = None
    mode: str = NONE
    said: str = ""

    @property
    def binding(self) -> bool:
        return self.mode == HARD and self.provider is not None

    def as_detail(self) -> dict:
        return {"provider": self.provider, "mode": self.mode, "said": self.said}


NOTHING = Preference()


def read(text: str) -> Preference:
    """What the owner asked for in this message, if anything.

    Silent by default. Most messages name no provider, and inventing one
    from a passing mention would take his choice away by guessing at it.
    """
    if not text:
        return NOTHING
    lowered = text.lower()

    found = None
    where = len(lowered) + 1
    for provider, names in NAMES.items():
        for name in names:
            at = lowered.find(name)
            if at != -1 and at < where:
                found, where = provider, at
    if found is None:
        return NOTHING

    # The provider has to be named as part of an instruction, and near
    # one. "Use Claude for this" counts; a sentence about Claude that
    # happens to contain the word "use" forty words earlier does not.
    window = lowered[max(0, where - 60):where + 40]
    if not _ASKED.search(window):
        return NOTHING

    mode = SOFT if any(word in window for word in _PREFERRING) else NONE
    if any(word in lowered for word in _BINDING):
        mode = HARD
    elif mode is NONE:
        # Named as an instruction with no hedge and no "only". Taken as a
        # firm choice: "use Claude for this" is an instruction, and
        # treating it as a hint would be the silent substitution this
        # whole module exists to prevent.
        mode = HARD

    return Preference(provider=found, mode=mode, said=text[:200])
