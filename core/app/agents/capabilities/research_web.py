"""research.web -- the first capability that touches the real world.

Everything before it was a mock returning a known string. This one holds
NETWORK, spends real money on every call, reaches a service that is
outside our control, and can fail in ways nobody scripted. That is the
point of building it first: it exercises permissions, cost, failure
handling and telemetry against reality rather than against a stub that
always cooperates.

It returns sources with every answer. A research agent whose claims
cannot be checked is not a research agent -- it is a confident voice, and
the whole system downstream would inherit its mistakes without any way of
noticing.
"""
import logging
from decimal import Decimal

from app.agents import registry
from app.agents.schemas import (
    AgentResult,
    AgentSpec,
    Handoff,
    Lifecycle,
    ModelTier,
    Permission,
)
from app.agents.tools import websearch
from app.config import get_settings

logger = logging.getLogger("jarvis.capabilities.research_web")

SPEC = AgentSpec(
    capability="research.web",
    name="Web research",
    description=(
        "Answers a question from current web sources and returns the pages "
        "it relied on."
    ),
    domain="research",
    task_types=("general", "research", "web"),  # genuinely general: any question
    tools=("websearch",),
    # NETWORK is the whole point. READ_MEMORY so a question can be
    # understood in the owner's terms; nothing else -- it reads the web
    # and reports, it does not act on what it finds.
    permissions=frozenset({Permission.NETWORK, Permission.READ_MEMORY}),
    model_tiers=(ModelTier.STANDARD, ModelTier.DEEP),
    status=Lifecycle.ACTIVE,
    # Working context only. A web question rarely needs the owner's
    # private life, and sending it would mean their personal facts
    # travelling to a search service for no benefit.
    config={"scopes": ["working"], "context_chars": 2000},
    max_cost_inr=Decimal("5.00"),
)


def _confidence(result: websearch.SearchResult) -> float:
    """How much to trust this, from how well it is supported.

    Deliberately mechanical. A model asked to rate its own certainty
    produces a number that sounds thoughtful and tracks nothing; counting
    sources at least measures something real. Anything downstream can
    compare these because they are all computed the same way.
    """
    sources = len(result.sources)
    if sources == 0:
        return 0.3   # an answer with nothing behind it
    if sources == 1:
        return 0.55  # one source is a lead, not a finding
    if sources < 4:
        return 0.7
    return 0.85      # never 1.0: the web agreed, which is not the same as true


async def run(handoff: Handoff, choice) -> AgentResult:
    settings = get_settings()

    question = handoff.objective
    if handoff.context:
        # The briefing goes in as background, not as the question. Folding
        # it into the query would search for the context instead of the
        # thing being asked.
        question = f"{handoff.objective}\n\nBackground:\n{handoff.context}"

    result = await websearch.search(
        question,
        granted=handoff.permissions,
        settings=settings,
        max_sources=int(handoff.constraints.get("max_sources", 8)),
    )

    unresolved = []
    if not result.sources:
        unresolved.append(
            "No sources were returned, so nothing here has been verified."
        )

    return AgentResult(
        output=result.answer,
        confidence=_confidence(result),
        evidence=result.sources,
        assumptions=(
            [f"Searched for: {q}" for q in result.queries] if result.queries else []
        ),
        unresolved=unresolved,
        next_action=(
            "Have these claims checked before acting on them."
            if result.sources else "Try a narrower question."
        ),
        model_used=result.model,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        cost_inr=Decimal(0),  # priced by the runtime, from one price list
    )


async def install() -> None:
    await registry.register(SPEC)
    registry.implement(SPEC.capability, run)
