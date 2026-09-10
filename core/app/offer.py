"""Noticing that a message wants doing, not answering.

JARVIS has two halves that did not know about each other. The chat box
answers from what the model already holds; the agents go and read the
live web and check what they find. Ask the same question in each and you
get different answers, and nothing tells you which one you are getting.

This is the join. While answering, the model marks any message whose
answer really needs looking up, and JARVIS offers to go and do it. The
owner says yes or ignores it.

Three decisions worth stating, because each rules out a worse version.

**The mark rides on the reply that was already being written.** No second
model call, so no extra waiting and no extra spend on a message the owner
was only chatting on. Classifying every message first would have cost a
round trip on "good morning", and the owner has twice said speed matters.

**Nothing runs without a yes.** The offer is the approval. The Tasks tab
exists for when a plan is worth reading before it runs; in conversation,
asking twice for one intent is just friction.

**The price is measured or absent.** What a run costs comes from what
runs like it have actually cost. Before there is any history JARVIS says
it does not know yet, rather than inventing a figure -- the same rule
that keeps it from inventing the income it does not track.
"""
import logging
import re

from app.agents import registry
from app.db import fetchrow

logger = logging.getLogger("jarvis.offer")

# What the model appends when it judges a message worth real work. Two
# brackets because a single one appears in ordinary prose; this does not.
MARKER = "JARVIS_CAN_DO"

# Deliberately forgiving: any bracket count, any spacing, upper or lower
# case, and a closing bracket that never arrived. A marker that leaks into
# the reply is the failure that matters -- the owner should never see the
# machinery -- so the pattern that removes it errs towards removing more.
_MARKER_RE = re.compile(
    r"\[{1,3}\s*" + MARKER + r"\s*[::]?\s*(?P<objective>[^\]]*?)\s*\]{0,3}\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# The two kinds of real work a conversation can start. LOOK_UP goes
# through the planner; MAKE goes to the Media Director, which runs a
# fixed chain with gates the planner is deliberately not allowed to
# assemble for itself.
LOOK_UP = "look_up"
MAKE = "make"
KINDS = (LOOK_UP, MAKE)
# The same thing when it lands mid-reply rather than at the end.
_MARKER_ANYWHERE_RE = re.compile(
    r"\[{1,3}\s*" + MARKER + r"\s*[::]?\s*(?P<objective>[^\]\n]*?)\s*\]{1,3}",
    re.IGNORECASE,
)

# Appended to the system prompt for every conversation. It is always
# present, and the server decides separately whether an offer is real by
# asking the registry -- so a marker for something JARVIS cannot do is
# stripped and quietly dropped rather than promised to the owner.
INSTRUCTION = f"""

You can do more than answer. Separately from this conversation there are
two things you can actually start, and only these two.

LOOK SOMETHING UP. Search the live web and check claims against sources.
Use it when your own knowledge is not good enough: it depends on current
information, the owner asked you to check or verify something, or they
told you plainly to go and find something out.

MAKE A PIECE OF CONTENT. Research a topic, verify the claims, decide
whether it is worth publishing, write it and have it reviewed. Use it
when the owner asks for a script, a post, a video, an article or content
about something. It publishes nothing: it produces a draft that waits for
them on the Media tab.

Do not write that content yourself instead. A draft you write here has
had no research behind it, nothing verified, no citations and nobody
reviewing it, which is exactly what the proper route exists to prevent.
Say in one sentence that you can put it together properly, mark it, and
stop. Do not say it is ready, do not say where it is, and do not describe
what it will contain.

When one of them applies, answer as best you can in one or two sentences,
say what you can do, and end your reply with exactly one of:

[[{MARKER}: {LOOK_UP} | a single self-contained sentence describing the job]]
[[{MARKER}: {MAKE} | the topic, as a single self-contained sentence]]

That line is machinery. The owner never sees it, so do not describe it,
apologise for it, or refer to it. Never put anything after it.

Do not use it for chat, for things about the owner you already hold in
memory, for opinions, or for anything a search cannot settle. Most
messages need no marker at all. Using it when it is not warranted spends
the owner's money on nothing, which is worse than not offering.
"""


def split(reply: str) -> tuple[str, str | None]:
    """Separate what the owner reads from what the machinery uses.

    Returns the cleaned reply, the objective if one was marked, and which
    of the two kinds of work it asks for.

    Never raises, and always strips. A reply that reaches the owner with
    "[[JARVIS_CAN_DO: ...]]" hanging off it is worse than one that misses
    an offer, so every path here removes the marker even when it cannot
    make sense of it.
    """
    if not reply or MARKER.lower() not in reply.lower():
        return reply, None, LOOK_UP

    objective = None
    match = _MARKER_RE.search(reply)
    if match:
        objective = (match.group("objective") or "").strip()
        reply = reply[: match.start()] + reply[match.end():]
    else:
        match = _MARKER_ANYWHERE_RE.search(reply)
        if match:
            objective = (match.group("objective") or "").strip()
            reply = _MARKER_ANYWHERE_RE.sub("", reply)

    # Whatever is left of a malformed marker goes too, rather than being
    # shown to the owner as if JARVIS had said it.
    if MARKER.lower() in reply.lower():
        reply = re.sub(r"\[*\s*" + MARKER + r".*$", "", reply,
                       flags=re.IGNORECASE | re.DOTALL)

    cleaned = re.sub(r"\n{3,}", "\n\n", reply).strip()
    if not objective:
        return cleaned, None, LOOK_UP

    kind, objective = _kind_of(objective)
    # A marker with nothing useful in it is not an offer. Falling back to
    # the owner's own message would be guessing at what they meant.
    return cleaned, (objective if len(objective) > 8 else None), kind


def _kind_of(objective: str) -> tuple[str, str]:
    """Read the kind off the front of a marked objective.

    Forgiving in the same direction as everything else here. A model that
    forgets the prefix gets the safer of the two: looking something up
    spends a little and changes nothing, while making a piece spends more
    and produces a draft nobody asked for.
    """
    if "|" not in objective:
        return LOOK_UP, objective
    head, rest = objective.split("|", 1)
    kind = head.strip().lower().replace(" ", "_").replace("-", "_")
    if kind in KINDS and rest.strip():
        return kind, rest.strip()
    return LOOK_UP, objective.strip()


# The chain a "make" offer runs. Named here so the check below asks about
# the capabilities that will actually be used, rather than about media in
# general -- a registry holding the scout but not the reviewer would pass
# a looser check and then produce something nobody reviewed.
_MEDIA_CHAIN = ("research.web", "factcheck.claims",
                "media.strategy", "media.script", "media.review")


async def can_act(kind: str = LOOK_UP) -> bool:
    """Is there actually an agent that could take this on?

    Asked of the registry rather than assumed, because the prompt always
    invites the marker and the agents may not have installed -- and an
    offer JARVIS cannot honour is worse than no offer.
    """
    try:
        if kind == MAKE:
            routable = {s.capability for s in await registry.find()}
            return all(c in routable for c in _MEDIA_CHAIN)
        return any(
            spec.capability != "general.writer"
            for spec in await registry.find(task_type="general")
        )
    except Exception:  # noqa: BLE001 - never worth failing a reply over
        return False


async def typical_cost(kind: str = LOOK_UP) -> float | None:
    """What work like this has actually cost, or None if nothing has run.

    The median of finished workflows rather than the mean: one expensive
    run should not make every future offer look dear. None is an honest
    answer and the caller says so in words -- a made-up figure about money
    is the one thing this project refuses to produce.

    Counted per kind, because the two are not comparable. A look-up is one
    or two steps; making a piece is five agents and sometimes a revision.
    Quoting one median for both would tell the owner a production costs
    what a search costs, which is the sort of wrong number that only shows
    up on the bill.
    """
    media = "EXISTS" if kind == MAKE else "NOT EXISTS"
    try:
        row = await fetchrow(
            f"""
            SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY spend) AS median
            FROM (
                SELECT SUM(t.spend_inr) AS spend
                FROM tasks t
                JOIN workflows w ON w.id = t.workflow_id
                WHERE w.status = 'completed'
                  AND {media} (
                      SELECT 1 FROM tasks m
                       WHERE m.workflow_id = w.id
                         AND m.capability LIKE 'media.%'
                  )
                GROUP BY t.workflow_id
                HAVING SUM(t.spend_inr) > 0
            ) runs
            """
        )
    except Exception:  # noqa: BLE001
        return None
    if row is None or row["median"] is None:
        return None
    return round(float(row["median"]), 2)


# What each kind actually does, in the owner's words. Shown on the card so
# the route is visible before it is agreed to: JARVIS choosing the wrong
# one should be something you can see and decline, not something you
# discover from the bill.
DOES = {
    LOOK_UP: "look this up properly",
    MAKE: "research, verify and draft this for you to approve",
}


async def build(objective: str, kind: str = LOOK_UP) -> dict | None:
    """Turn a marked objective into the offer the owner is shown."""
    if kind not in KINDS:
        kind = LOOK_UP
    if not objective or not await can_act(kind):
        return None

    cost = await typical_cost(kind)
    return {
        "objective": objective,
        "kind": kind,
        "does": DOES[kind],
        # Said in words rather than as a number the caller has to
        # interpret, so an unknown price reads as unknown.
        "cost_note": (
            f"about Rs.{cost:.2f}, going by what runs like this have cost"
            if cost is not None
            else "this would be the first one, so I have no measurement yet"
        ),
        "typical_cost_inr": cost,
    }


async def remember_outcome(objective: str, summary: str) -> None:
    """Keep what the work found, so tomorrow JARVIS still knows it.

    Stored 'retrieved': it is material that came back from outside, not
    something the owner said and not JARVIS's own inference. Without this
    a research run would answer the question and be forgotten by the next
    message, which is the exact failure long-term memory exists to stop.
    """
    from app.memory import store_memory

    text = summary.strip()
    if not text:
        return
    try:
        await store_memory(
            content=f"Looked into: {objective.strip()}\n\n{text[:4000]}",
            category="project",
            origin="retrieved",
        )
    except Exception as exc:  # noqa: BLE001 - never worth failing the work over
        logger.warning("Could not remember what the work found: %s", exc)


# --- the spoken path -------------------------------------------------------
#
# Voice cannot use the marker. Gemini Live generates speech, so a marker in
# its output is a marker read out loud -- brackets and all. It is told to
# offer in plain words instead ("I can look that up, sir -- shall I?"), and
# the button that makes that actionable comes from here: JARVIS reads back
# what the owner *said* and decides separately.
#
# That means a model call per spoken turn, which is a real cost on a free
# quota. So a free filter runs first and most turns never reach the model.

_ASKING = (
    "look up", "look it up", "find out", "search", "google", "check",
    "verify", "fact check", "is it true", "latest", "current", "right now",
    "today", "this week", "news", "what happened", "how much is",
    "how much does", "price of", "confirm",
)

_SPEECH_PROMPT = """\
The owner of a personal assistant said this out loud. Decide whether
answering it honestly needs looking something up on the live web --
because it depends on current information, because they asked for
something to be checked or verified, or because they told the assistant
to go and find something out.

Say no to chat, to opinions, to anything about the owner the assistant
would already know, and to anything a search cannot settle. No is the
common answer and the safe one: saying yes when it is not warranted
spends the owner's money on nothing.

They said: {said}

Reply with JSON only:
{{"needed": true or false, "objective": "one self-contained sentence"}}
"""


def looks_like_a_request(said: str) -> bool:
    """The free half of the decision.

    A keyword filter, not a judgement -- its only job is to keep most
    spoken turns from costing a model call at all. It is deliberately
    loose: a false positive costs one cheap call, a false negative costs
    an offer that was never made.

    It reads English cues, which catches the owner's usual mixed speech
    because the verb tends to arrive in English. A request made entirely
    in another script will be missed, and that is a known limit rather
    than an accident.
    """
    text = (said or "").lower()
    return len(text.split()) >= 3 and any(cue in text for cue in _ASKING)


async def from_speech(said: str, provider) -> str | None:
    """What the owner asked for out loud, if it wants real work.

    Returns an objective, or None -- and never raises. This runs while the
    owner is mid-conversation, so a bad JSON day must cost a missed offer
    and nothing else.
    """
    import json

    if not looks_like_a_request(said) or not await can_act():
        return None

    try:
        result = await provider.complete(_SPEECH_PROMPT.format(said=said[:1000]))
        text = result.text.strip()
        fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
        if fenced:
            text = fenced.group(1).strip()
        else:
            start, end = text.find("{"), text.rfind("}")
            if start != -1 and end > start:
                text = text[start : end + 1]
        data = json.loads(text)
    except Exception as exc:  # noqa: BLE001 - a missed offer, nothing more
        logger.info("Could not read a spoken request: %s", exc)
        return None

    if not isinstance(data, dict) or not data.get("needed"):
        return None
    objective = str(data.get("objective") or "").strip()
    return objective if len(objective) > 8 else None


# --- answering out loud ----------------------------------------------------
#
# The first version of the spoken path asked a question and would only take
# a button as the answer. Said aloud, "yes, go ahead" reached nothing: the
# work never started, JARVIS never learned that it had offered, and it
# asked again -- and again. A question asked in speech has to be
# answerable in speech.
#
# Matched rather than modelled. This runs between two turns of a live
# conversation, where a model call is a pause the owner hears, and the
# failure mode is mild: an unrecognised answer leaves the offer standing,
# exactly as if nothing had been said.

_YES = (
    "yes", "yeah", "yep", "yup", "ya", "sure", "ok", "okay", "please",
    "go ahead", "go for it", "do it", "carry on", "proceed", "of course",
    "absolutely", "definitely", "haan", "han", "ho", "houdu", "sari",
    "theek hai", "thik hai", "kar do", "karo", "check karo", "dekho",
)
_NO = (
    "no", "nope", "nah", "not now", "later", "leave it", "forget it",
    "don't", "do not", "never mind", "nevermind", "cancel", "stop",
    "nahi", "nako", "beda", "bedi",
)


def _leading_words(said: str, count: int = 6) -> str:
    text = re.sub(r"[^\w\s]", " ", (said or "").lower())
    return " ".join(text.split()[:count])


def reads_as_yes(said: str) -> bool:
    """Did the owner just agree to the thing that was offered?

    Only the opening words are read. "Yes, and while you're at it..." is
    an acceptance; a "yes" buried in the middle of a different sentence is
    not, and treating it as one would start work nobody asked for.
    """
    head = _leading_words(said)
    if not head:
        return False
    # No before yes: "no, don't" contains neither trap, but "not yes" and
    # similar should never start work.
    if any(re.match(rf"\b{re.escape(word)}\b", head) for word in _NO):
        return False
    return any(re.match(rf"\b{re.escape(word)}\b", head) for word in _YES)


def reads_as_no(said: str) -> bool:
    head = _leading_words(said)
    return bool(head) and any(
        re.match(rf"\b{re.escape(word)}\b", head) for word in _NO
    )
