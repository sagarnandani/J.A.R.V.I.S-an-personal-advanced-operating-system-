"""When JARVIS may consult the live web, and what it owes you if it did not.

Section 24. Four modes, and the one that matters is `required`.

    off        Do not search. Answer from what is already known, and say
               that is what this is.
    optional   Search when it would help. If it fails, carry on without.
    required   Search, and if it cannot, FAIL. Do not answer.
    fallback   Answer from what is known; search only if that is not enough.

**Why `required` earns its place.** A research agent whose search comes
back empty still has a model that will happily produce an answer, and
that answer is indistinguishable in tone from a researched one. JARVIS
already labels it -- but a label is something the owner has to read, and
on some questions "no answer" is the only correct output. `required` is
how you say this is one of those: a monthly subsidy figure, a deadline, a
price. Getting it from the model's memory is worse than not getting it.

**What this does NOT govern.** Opening a page the owner named. That is
following an instruction, not searching, and `off` does not countermand
an instruction. Reaching the network at all is governed by the NETWORK
permission, which is a different and stricter thing: policy decides
whether to search, permission decides whether it is possible.

**Precedence**, narrowest first, because the specific should beat the
general:

    the task's own constraint  >  the agent's registered default
                              >  the server setting
"""
import logging
from enum import Enum

logger = logging.getLogger("jarvis.search_policy")


class Search(str, Enum):
    OFF = "off"
    OPTIONAL = "optional"
    REQUIRED = "required"
    FALLBACK = "fallback"


DEFAULT = Search.OPTIONAL

# In the owner's words, for the dashboard and for a refusal that has to
# explain itself.
DESCRIBED: dict[Search, str] = {
    Search.OFF: "Never search the web; answer from what is already known.",
    Search.OPTIONAL: "Search when it helps; carry on if the search fails.",
    Search.REQUIRED: "Must search. If it cannot, say so rather than answer.",
    Search.FALLBACK: "Answer from what is known; search only if that is not enough.",
}


def read(value, fallback: Search = DEFAULT) -> Search:
    """One of the four, from whatever was written.

    An unrecognised mode is the default and a log line, not a crash. A
    typo in a constraint should not take out a task -- but it must not
    silently read as `off` either, which would look exactly like a task
    that quietly stopped checking its facts.
    """
    if isinstance(value, Search):
        return value
    if value is None or value == "":
        return fallback
    try:
        return Search(str(value).strip().lower())
    except ValueError:
        logger.info("Unknown search policy %r; using %s.", value, fallback.value)
        return fallback


def resolve(constraints: dict | None, spec=None, settings=None) -> Search:
    """The policy in force for one task. Narrowest wins."""
    named = (constraints or {}).get("search")
    if named is not None and named != "":
        return read(named)

    registered = (getattr(spec, "config", None) or {}).get("search")
    if registered is not None and registered != "":
        return read(registered)

    return read(getattr(settings, "search_policy", None))


def may_search(policy: Search) -> bool:
    """Is a search allowed at all under this policy."""
    return policy is not Search.OFF


def must_search(policy: Search) -> bool:
    """Is an answer without a search a failure rather than a caveat."""
    return policy is Search.REQUIRED


def explain(policy: Search) -> str:
    return DESCRIBED[policy]
