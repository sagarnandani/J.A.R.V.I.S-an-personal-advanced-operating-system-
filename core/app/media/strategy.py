"""media.strategy -- whether this should exist at all.

The one agent whose most valuable answer is no. Everything upstream has
already cost money, which is exactly the pressure that makes a system
publish weak work: the research is done, so something may as well come of
it. That is how a content spam factory starts.

So it is deliberately allowed, and expected, to decline -- and its
decline ends the workflow cleanly with a reason, rather than being
treated as a failure.

It is separate from the Script agent for the same reason the Review agent
is: a writer asked whether to write always says yes.
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
from app.media import _common, brands

logger = logging.getLogger("jarvis.media.strategy")

SPEC = AgentSpec(
    capability="media.strategy",
    name="Content strategy",
    description=(
        "Decides whether researched and verified material should become "
        "content at all, and if so for which brand, platform and format, "
        "with the angle and the hook."
    ),
    domain="media",
    supervisor="media.director",
    task_types=("media", "strategy"),
    permissions=frozenset({Permission.READ_MEMORY}),
    model_tiers=(ModelTier.STANDARD, ModelTier.DEEP),
    status=Lifecycle.ACTIVE,
    config={"scopes": ["working", "project", "shared"], "context_chars": 6000},
    max_cost_inr=Decimal("6.00"),
)

_PROMPT = """\
You decide what a small AI-native media company should and should not
publish. You are not writing anything; you are deciding whether writing
is justified.

The two properties:

{sagar}

---

{ai_media}

---

Research and verification results:
{brief}

Decide. Saying "do not publish" is a good answer and a common one --
research already spent is not a reason to publish. Trending is not the
same as useful.

If you recommend publishing, the `reason_to_exist` must say what a viewer
gets that they would not get from the headline. If you cannot write that
honestly, recommend against.

Where verification found claims that are unsupported, misleading or
disputed, either build the angle around that honestly or decline. Never
recommend an angle the evidence does not support.

`reuse` lists further pieces the same research could honestly support --
only where each has its own reason to exist, never to multiply output.

Reply with JSON only:
{{"publish": true or false,
  "why": "one or two sentences",
  "brand": "sagar" or "ai_media",
  "platform": "youtube" or "youtube_shorts" or "instagram_reel" or "instagram_carousel",
  "format": "explainer" or "news" or "verification" or "experiment" or "tool_review",
  "angle": "...", "hook": "...", "viewer_value": "...",
  "reason_to_exist": "...",
  "reuse": [{{"platform": "...", "angle": "...", "why": "..."}}]}}
"""


async def run(handoff: Handoff, choice) -> _common.AgentResult:
    brief = _common.briefing(handoff)
    if not brief:
        return _common.result(
            {"summary": "Nothing to decide on: no research reached this step.",
             "publish": False},
            confidence=0.2, model=None,
            unresolved=["No research or verification was provided."],
            next_action="Run research and verification first.",
        )

    data, model = await _common.think(
        _PROMPT.format(
            sagar=brands.voice(brands.PERSONAL),
            ai_media=brands.voice(brands.AI_MEDIA),
            brief=brief[:12000],
        ),
        "media.strategy",
    )

    publish = bool(data.get("publish"))
    why = str(data.get("why") or "").strip()

    if not publish:
        return _common.result(
            {"summary": f"Recommend not publishing. {why}", "publish": False,
             "why": why},
            # A confident no is worth as much as a confident yes, and is
            # usually better supported: it takes less to know something is
            # not worth making.
            confidence=0.75, model=model,
            next_action="Nothing further. The scout's queue holds the next one.",
        )

    brand = str(data.get("brand") or "").lower()
    if brand not in brands.BRANDS:
        brand = brands.AI_MEDIA

    decision = {
        "summary": (f"Publish as {data.get('format', '?')} on "
                    f"{data.get('platform', '?')} for {brands.BRANDS[brand]['name']}. "
                    f"Angle: {data.get('angle', '')}"),
        "publish": True, "why": why, "brand": brand,
        "platform": str(data.get("platform") or "youtube"),
        "format": str(data.get("format") or "explainer"),
        "angle": str(data.get("angle") or ""),
        "hook": str(data.get("hook") or ""),
        "viewer_value": str(data.get("viewer_value") or ""),
        "reason_to_exist": str(data.get("reason_to_exist") or ""),
        "reuse": [r for r in (data.get("reuse") or []) if isinstance(r, dict)],
    }
    return _common.result(
        decision, confidence=0.7, model=model,
        next_action=f"Script it for {decision['platform']}.",
        unresolved=([] if decision["reason_to_exist"] else
                    ["No clear reason this should exist beyond the headline."]),
    )


async def install() -> None:
    await registry.register(SPEC)
    registry.implement(SPEC.capability, run)
