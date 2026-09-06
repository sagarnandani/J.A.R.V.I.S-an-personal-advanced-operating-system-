"""Searching the live web, through Gemini's own grounding.

Why this and not a search API: the owner already has a Gemini key on a
free tier. Brave, Serper and Tavily would each mean another account,
another key to store and another bill -- friction this project has had
enough of. Google's grounding returns both an answer and the sources it
used, which is exactly what a research capability owes its caller.

What comes back is evidence, not truth. The URLs are recorded so a claim
can be checked, because "the model said so" is not a citation and a
research agent that cannot be audited is worse than none.
"""
import logging
from dataclasses import dataclass, field

from google import genai
from google.genai import types

from app.agents.schemas import AgentError, Permission, PermissionDenied

logger = logging.getLogger("jarvis.tools.websearch")


@dataclass
class SearchResult:
    answer: str
    sources: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""


class SearchUnavailable(AgentError):
    """The search could not run. Retryable unless we know better."""


def _classify(exc: Exception) -> AgentError:
    """Turn a provider error into something the runtime can act on.

    A deny-list, for the reason this project learned the hard way: listing
    the failures worth retrying means every failure nobody imagined gets
    treated as permanent. Naming the few that are definitely not worth
    retrying means an unfamiliar error costs one retry and still works.
    """
    text = str(exc).lower()
    permanent = (
        "api_key_invalid", "permission_denied", "unauthenticated",
        "not_found", "invalid_argument",
    )
    if any(marker in text for marker in permanent):
        return SearchUnavailable(f"Web search cannot run: {exc}", retryable=False)
    if "resource_exhausted" in text or "quota" in text or "429" in text:
        # Retryable, but not immediately useful -- the quota resets on
        # its own clock, so the retry budget will be spent for nothing.
        return SearchUnavailable(
            f"Web search quota is exhausted for now: {exc}", retryable=False
        )
    return SearchUnavailable(f"Web search failed: {exc}")


async def search(
    query: str,
    *,
    granted: frozenset[Permission],
    settings,
    max_sources: int = 8,
) -> SearchResult:
    """Ask the live web a question, and record where the answer came from.

    `granted` is the permission set of the agent making the call. Passing
    it is not ceremony: it is what makes reaching the network impossible
    without holding NETWORK, wherever the call is made from.
    """
    if Permission.NETWORK not in granted:
        raise PermissionDenied(
            "Reaching the web needs the 'network' permission, which this "
            "agent does not hold."
        )
    if not settings.gemini_api_key:
        raise SearchUnavailable(
            "Web search needs GEMINI_API_KEY on the server.", retryable=False
        )
    if not query.strip():
        raise SearchUnavailable("Nothing to search for.", retryable=False)

    client = genai.Client(api_key=settings.gemini_api_key)
    model = settings.search_model or settings.gemini_model

    try:
        response = await client.aio.models.generate_content(
            model=model,
            contents=query,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                system_instruction=(
                    "Answer from current web sources. State what is "
                    "established and what is disputed. If the sources "
                    "disagree, say so rather than choosing one."
                ),
            ),
        )
    except Exception as exc:  # noqa: BLE001 - classified, then re-raised
        raise _classify(exc) from exc

    return SearchResult(
        answer=response.text or "",
        sources=_sources(response, max_sources),
        queries=_queries(response),
        tokens_in=_usage(response, "prompt_token_count"),
        tokens_out=(
            _usage(response, "candidates_token_count")
            + _usage(response, "thoughts_token_count")
        ),
        model=model,
    )


def _grounding(response):
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return None
    return getattr(candidates[0], "grounding_metadata", None)


def _sources(response, limit: int) -> list[str]:
    """The pages the answer actually rested on.

    Read defensively at every level: grounding metadata is optional, and a
    research result losing its citations because one field was absent
    would be worse than useless -- it would look verified.
    """
    meta = _grounding(response)
    chunks = getattr(meta, "grounding_chunks", None) or [] if meta else []
    out: list[str] = []
    for chunk in chunks:
        web = getattr(chunk, "web", None)
        if web is None:
            continue
        uri = getattr(web, "uri", None)
        title = getattr(web, "title", None) or uri
        if uri and uri not in [s.split(" — ")[-1] for s in out]:
            out.append(f"{title} — {uri}" if title != uri else uri)
        if len(out) >= limit:
            break
    return out


def _queries(response) -> list[str]:
    """What it actually searched for -- part of showing its working."""
    meta = _grounding(response)
    return list(getattr(meta, "web_search_queries", None) or []) if meta else []


def _usage(response, field: str) -> int:
    usage = getattr(response, "usage_metadata", None)
    return getattr(usage, field, None) or 0 if usage else 0
