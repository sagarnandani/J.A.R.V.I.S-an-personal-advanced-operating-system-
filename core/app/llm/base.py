"""Provider-agnostic LLM interface.

This is the seam the architecture doc's section M and the Stage 0 brief's
section 4.2 both call for: "structure this behind an interface so a second
provider can be added later without touching calling code." Nothing
outside this package should ever import a provider SDK directly.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass

# One definition, shared by every provider -- JARVIS should behave the same
# whichever model is answering. If this differed per adapter, switching
# providers would quietly change JARVIS's personality along with it.
#
# The instruction not to guess is doing real work now that memory is fed
# back in. A model handed a partial history will cheerfully invent the
# rest, and an assistant that fabricates what you told it is worse than
# one that admits it has lost the thread.
JARVIS_SYSTEM_PROMPT = (
    "You are JARVIS, a personal AI operating system serving one owner. "
    "Address him as 'sir'.\n\n"
    "Manner: composed, precise, understated. Brief by default -- a "
    "sentence or two unless more is genuinely needed. Dry wit is welcome; "
    "enthusiasm is not. Never open with filler ('Certainly!', 'Great "
    "question!'), never say 'As an AI', never apologise for what you are. "
    "You are staff, not a chatbot: you answer, you do not perform.\n\n"
    "When the owner arrives -- 'daddy's home', 'I'm back', 'good morning' "
    "-- greet him properly and, if there is anything worth reporting, give "
    "a short status. 'Welcome back, sir.' is the register.\n\n"
    "Earlier turns of this conversation are provided to you from a memory "
    "store, so you do remember what was said before, including in earlier "
    "sessions -- do not claim otherwise. But remember only what is "
    "actually there: if something is not in what you were given, say you "
    "do not have it rather than inventing it. This applies absolutely to "
    "figures. Never estimate, guess or illustrate a number -- money "
    "earned, money spent, tasks finished. If a figure was not given to "
    "you, say it is not being tracked yet.\n\n"
    "Always reply in the same language the owner wrote or spoke in -- "
    "Kannada, Hindi, Marathi, English, Spanish, whatever they used -- and "
    "in the same script. If they mix languages, mix them back the same "
    "way. Never switch to English because it is easier."
)

# Appended rather than written inline so the persona above stays readable
# and the two can be changed without disturbing each other. See
# app/offer.py for why the mark rides on the reply instead of costing a
# second call.
from app.offer import INSTRUCTION as _CAN_DO_INSTRUCTION  # noqa: E402

# Kept separately so the spoken path can use the persona without the
# marker. A model reading "[[JARVIS_CAN_DO: ...]]" aloud is not a subtle
# failure, and it would have been every voice reply that wanted looking up.
_PROMPT_WITHOUT_OFFERS = JARVIS_SYSTEM_PROMPT
JARVIS_SYSTEM_PROMPT += _CAN_DO_INSTRUCTION


USER = "user"
ASSISTANT = "assistant"


def system_prompt_with(memory_context: str | None, *, offers: bool = True) -> str:
    """The system prompt, plus what JARVIS knows about its owner.

    Labelled as JARVIS's own notes rather than presented as fact. They are
    summaries it wrote of things the owner said, and the instruction to
    trust the owner over its own notes is what stops a stale note winning
    an argument with the person it is about.

    `offers=False` removes the instruction that has the model mark work it
    could do. That mark is written text, invisible in a typed reply and
    stripped before anybody sees it -- but a model that is *speaking*
    would read the brackets out loud. Voice therefore asks in words
    instead; see app/routes/live.py.
    """
    prompt = JARVIS_SYSTEM_PROMPT if offers else _PROMPT_WITHOUT_OFFERS
    if not memory_context:
        return prompt
    return (
        f"{prompt}\n\n"
        "These are your own notes about your owner, from earlier "
        "conversations. Use them when relevant, and do not announce them "
        "unprompted. If the owner says something that contradicts a note, "
        "the owner is right:\n"
        f"{memory_context}"
    )


@dataclass(frozen=True)
class Turn:
    """One thing that was said, by one side of the conversation.

    Deliberately not the database row: a memory carries provenance,
    confidence and links that a model has no use for. This is only what a
    provider needs to reconstruct the conversation, which keeps the
    memory store's shape from leaking into every adapter.
    """

    role: str  # USER or ASSISTANT
    text: str


def normalise_history(turns: list[Turn]) -> list[Turn]:
    """Make a history that every provider will accept.

    Providers expect a conversation to start with the user and to
    alternate. Real history doesn't always: trimming to a size limit can
    slice a pair in half and leave a reply with nothing before it, which
    Claude rejects outright with an error that says nothing about why.

    So two rules, applied here once rather than in each adapter:
      * drop leading assistant turns -- a reply to a question we no longer
        have is not worth the tokens;
      * collapse consecutive turns from the same side into one, which can
        only happen if something was stored oddly, but costs a request if
        it ever does.
    """
    cleaned: list[Turn] = []
    for turn in turns:
        if not turn.text.strip():
            continue
        if not cleaned and turn.role != USER:
            continue
        if cleaned and cleaned[-1].role == turn.role:
            merged = Turn(role=turn.role, text=f"{cleaned[-1].text}\n\n{turn.text}")
            cleaned[-1] = merged
            continue
        cleaned.append(turn)
    return cleaned


@dataclass
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    model: str
    provider: str


class LLMProvider(ABC):
    @abstractmethod
    async def complete(
        self,
        message: str,
        history: list[Turn] | None = None,
        memory_context: str | None = None,
    ) -> LLMResult:
        """Send a message in the context of what came before.

        `history` is oldest-first and excludes `message` itself.

        `memory_context` is long-term memory -- durable facts, which may
        come from months ago. It goes into the system instruction rather
        than being faked as conversation, because that is what it is:
        standing knowledge, not something anybody said just now.
        """
