"""Catching JARVIS saying it did something it did not do.

Three times now the owner has been told about work that never happened.
It said it was displaying a script on screen. It said a piece was waiting
on the Media tab. It said it was fetching the latest information and then
answered from two-year-old training data. Every one of those was the same
failure: the model knows the capability exists, cannot reach it from a
conversation, and narrates using it instead.

The prompt is told not to. That helps and it is not enough, because a
model that has just been handed a list of its own capabilities will
describe using them, and a prompt rule is advice rather than a mechanism.
So this is the mechanism: the reply is checked before it reaches the
owner, and a claim that nothing backs gets one honest sentence after it.

Two rules keep this from becoming its own problem.

**It only ever adds.** The model's words are never rewritten or deleted.
A guard that edits replies would eventually mangle a good one, and there
would be no way to tell from the outside that it had.

**It only fires when nothing is behind the claim.** When JARVIS offered
to do the work, "I'll look that up" is true and the card underneath it is
the proof. The guard is about the case where there is no card.
"""
import logging
import re

logger = logging.getLogger("jarvis.claims")

# Claims of work in progress or already done. Deliberately narrow: each
# pattern is a phrase that has actually appeared, or its close relatives.
# A wide net here would fire on ordinary replies and the correction would
# become the noise instead of the fix.
_CLAIMS: tuple[tuple[str, str], ...] = (
    # "I'm looking that up now", "I am fetching the latest figures"
    (r"\b(?:i'?m|i am)\s+(?:just\s+|now\s+|currently\s+)?"
     r"(?:search|look|fetch|check|pull|gather|retriev|scan|draft|writ|"
     r"prepar|generat|creat|compil|put)\w*\b", "work in progress"),
    # "I've written it", "I have prepared the script", "I've posted it"
    (r"\b(?:i'?ve|i have)\s+(?:just\s+|already\s+)?"
     r"(?:search|look|fetch|check|writ|draft|prepar|generat|creat|sav|"
     r"post|sent|publish|display|shown|show|put|added|upload)\w*\b",
     "work already done"),
    # "let me look that up", with no mechanism behind it
    (r"\blet me\s+(?:just\s+)?"
     r"(?:search|look|fetch|check|pull|gather|draft|writ|prepar|generat)\w*\b",
     "work in progress"),
    # Asking the owner to wait for something that is not happening
    (r"\b(?:one moment|just a (?:moment|second|minute)|"
     r"give me a (?:moment|second|minute)|bear with me|hold on)\b",
     "asking the owner to wait"),
    # "It's in process", "still working on it", "it's underway". The first
    # version of this list caught the first person and the model moved to
    # the third: asked why a script was slow, it said the work was in
    # progress and that some topics take longer to research. Neither
    # sentence contains "I".
    (r"\b(?:in process|in progress|under way|underway|being (?:worked|"
     r"prepared|drafted|written|researched|generated))\b",
     "work said to be under way"),
    (r"\b(?:still )?(?:working|running) on (?:it|that|this)\b",
     "work said to be under way"),
    (r"\b(?:almost|nearly) (?:ready|done|finished)\b",
     "work said to be nearly done"),
    (r"\b(?:shortly|any moment now|in a (?:few|couple of) minutes)\b",
     "promising something soon"),
    # The worst of them: an explanation invented for work that is not
    # happening. "Some topics take longer to research" is a reason given
    # for a delay that does not exist.
    (r"\b(?:take[sn]?|taking) (?:a bit )?longer\b", "explaining a delay"),
    (r"\btake[sn]? (?:more|some) time\b", "explaining a delay"),
    # "it's on the Media tab", "you'll find it on your screen"
    (r"\b(?:on|in|under) the media tab\b", "pointing at the Media tab"),
    (r"\bon (?:your|the) screen\b", "pointing at a screen"),
)

_COMPILED = tuple((re.compile(p, re.IGNORECASE), why) for p, why in _CLAIMS)

# What gets added. Written as JARVIS would say it, because it appears in
# JARVIS's own reply -- and short, because it is a correction and not a
# lecture.
CORRECTION = (
    "To be accurate, sir: none of that has actually happened. I cannot "
    "run anything from a conversation unless I offer it and you accept."
)


def unbacked(reply: str) -> str | None:
    """What the reply claims, if it claims something nothing backs.

    Returns a short description of the claim, or None. Never raises: a
    guard that can break a reply is worse than the thing it guards
    against.
    """
    if not reply:
        return None
    try:
        for pattern, why in _COMPILED:
            found = pattern.search(reply)
            if found:
                return why
    except Exception:  # noqa: BLE001
        return None
    return None


def correct(reply: str, *, offered: bool, in_flight: bool = False) -> tuple[str, str | None]:
    """Add the honest sentence, if one is needed.

    Two ways a claim can be true, and the guard must stay quiet for both.

    `offered` -- there is a card on screen, so "I'll look that up" is a
    statement about a button the owner is looking at.

    `in_flight` -- something genuinely is running. Once JARVIS's status
    notes carry work in progress, "it is still being researched" stops
    being a fabrication and becomes a report, and a guard that corrected
    it would be the one lying.
    """
    if offered or in_flight or not reply:
        return reply, None

    why = unbacked(reply)
    if why is None:
        return reply, None

    logger.info("Corrected an unbacked claim (%s): %.160s", why, reply)
    return f"{reply.rstrip()}\n\n{CORRECTION}", why


# When the model marked work but the registry cannot take it. Until now
# this was logged on the server and nothing else: the owner had just been
# told something was coming, no card appeared, and there was nothing on
# screen to say why. Silence is the worst of the three outcomes.
UNAVAILABLE = {
    "look_up": (
        "I marked that to look up properly, but the capability that does "
        "it is not available on this deployment, so nothing has started."
    ),
    "make": (
        "I marked that to research and draft, but the media chain is not "
        "fully registered on this deployment, so nothing has started and "
        "there will be nothing on the Media tab."
    ),
}


def nothing_registered(reply: str, kind: str) -> str:
    """Say plainly that the work was marked and cannot run.

    The owner has just read a sentence saying JARVIS will do something. A
    dropped offer makes that sentence a lie by omission, and the fix is
    one line rather than a log entry only the server can see.
    """
    note = UNAVAILABLE.get(kind, UNAVAILABLE["look_up"])
    return f"{(reply or '').rstrip()}\n\n{note}".strip()
