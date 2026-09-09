"""media.scout -- what might be worth making something about.

The brief asks for continuous monitoring of announcements, publications,
trends and viral claims. What it must not do is turn everything trending
into content: the output is a short ranked queue with reasons, not a
hundred links.

Two things shape it.

**Ranking is the product.** Anyone can list what happened today. The
value is in saying which two of those are worth spending research money
on, and being willing to say none of them are.

**It scores against what has already been covered.** Saturation is one of
the criteria, and a scout with no memory of its own output will keep
proposing the same story every morning.

One honest limit: this is only as good as the search underneath it, and
web search on the current free tier has been returning stale results. The
architecture is right and the scout improves the day that does; it is not
pretending to be better than its inputs today.
"""
import logging
from decimal import Decimal

from app.agents import registry
from app.agents.schemas import (
    AgentSpec,
    Handoff,
    Lifecycle,
    ModelTier,
    Permission,
)
from app.agents.tools import websearch
from app.config import get_settings
from app.media import _common

logger = logging.getLogger("jarvis.media.scout")

MAX_OPPORTUNITIES = 5

SPEC = AgentSpec(
    capability="media.scout",
    name="Opportunity scout",
    description=(
        "Finds recent developments worth making content about and returns a "
        "short ranked queue with a reason for each. Says so when nothing is "
        "worth covering."
    ),
    domain="media",
    supervisor="media.director",
    task_types=("media", "scouting"),
    tools=("websearch",),
    permissions=frozenset({Permission.NETWORK, Permission.READ_MEMORY}),
    model_tiers=(ModelTier.STANDARD,),
    status=Lifecycle.ACTIVE,
    # Shared scope carries what has already been covered, which is what
    # lets it score saturation instead of proposing the same story daily.
    config={"scopes": ["working", "shared"], "context_chars": 3000},
    max_cost_inr=Decimal("6.00"),
)

_PROMPT = """\
You scout for a small AI-native media company. Your job is to decide what
is worth spending research money on, not to list everything that happened.

Theme to watch: {theme}

What recent search returned:
{found}

{covered}

Return at most {limit} opportunities, best first. Score each 0-1:

- relevance      how close to the theme
- freshness      how recent and still moving
- credibility    how solid the sourcing looks so far
- saturation     HOW HEAVILY COVERED ALREADY (high is bad)
- novelty        is there an angle nobody is taking
- strategic_fit  does it suit this company's two properties

`brand` is "sagar" for building-and-experimenting content, "ai_media" for
news, verification and tool analysis.

`why_now` is one sentence on why this is worth attention today.
`reason_to_exist` is one sentence on what a viewer gets that they would
not get from the headline. If you cannot write that sentence honestly,
leave the opportunity out.

An empty list is a correct answer. Trending is not the same as useful,
and proposing weak work costs the owner real research money.

Reply with JSON only:
{{"opportunities": [{{"title": "...", "source": "...", "why_now": "...",
  "reason_to_exist": "...", "brand": "sagar" or "ai_media",
  "relevance": 0.0, "freshness": 0.0, "credibility": 0.0,
  "saturation": 0.0, "novelty": 0.0, "strategic_fit": 0.0}}],
 "nothing_worth_covering": false, "note": "..."}}
"""


def rank(opportunity: dict) -> float:
    """One number, computed the same way every time.

    Saturation counts against. A heavily covered story needs a much
    better angle to be worth the same money as a fresh one, and letting
    the model hand back its own overall score would make two days'
    queues incomparable.
    """
    def score(key: str) -> float:
        try:
            return max(0.0, min(1.0, float(opportunity.get(key, 0))))
        except (TypeError, ValueError):
            return 0.0

    return round(
        0.25 * score("relevance")
        + 0.20 * score("freshness")
        + 0.15 * score("credibility")
        + 0.15 * score("novelty")
        + 0.15 * score("strategic_fit")
        - 0.20 * score("saturation"),
        3,
    )


async def run(handoff: Handoff, choice) -> _common.AgentResult:
    settings = get_settings()
    theme = handoff.objective
    limit = int((handoff.constraints or {}).get("max_opportunities", MAX_OPPORTUNITIES))

    found = await websearch.search(
        f"Recent notable developments: {theme}. What happened in the last few "
        f"days, and from which primary sources?",
        granted=handoff.permissions, settings=settings, max_sources=8,
    )

    covered = _common.briefing(handoff)
    data, model = await _common.think(
        _PROMPT.format(
            theme=theme, limit=limit,
            found=found.answer or "(the search returned nothing)",
            covered=(f"Already covered recently -- score these as saturated:\n{covered}"
                     if covered else "Nothing covered recently."),
        ),
        "media.scout",
    )

    raw = data.get("opportunities") or []
    ranked = sorted(
        ({**o, "score": rank(o)} for o in raw if isinstance(o, dict) and o.get("title")),
        key=lambda o: o["score"], reverse=True,
    )[:limit]

    summary = (
        f"Scanned {theme}. {len(raw)} candidate(s), {len(ranked)} worth ranking."
        if ranked else
        f"Scanned {theme}. Nothing found worth spending research money on."
    )

    return _common.result(
        {"summary": summary, "opportunities": ranked,
         "note": str(data.get("note") or "")},
        # Never certain: a scout is guessing at what an audience will care
        # about, and its own sources may be stale.
        confidence=0.6 if ranked else 0.4,
        model=model,
        evidence=found.sources,
        unresolved=([] if ranked else
                    ["Nothing met the bar. That is a result, not a failure."]),
        next_action=(f"Research: {ranked[0]['title']}" if ranked
                     else "Wait for something better."),
    )


async def install() -> None:
    await registry.register(SPEC)
    registry.implement(SPEC.capability, run)
