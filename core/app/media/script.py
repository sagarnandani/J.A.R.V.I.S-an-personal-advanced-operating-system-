"""media.script -- the original writing.

Works from the verified brief, never from its own recollection. Every
factual line carries the claim it rests on, which is what makes the
Review agent able to check the script against the evidence rather than
against its own opinion of whether the script sounds right.

That citation requirement is also the anti-plagiarism mechanism. A script
built line by line from verified claims cannot be a lightly rewritten
transcript, because the claims came from the research and the sentences
have to be answerable to them.

The voice comes from the brand the Strategy agent chose, so the personal
brand and the AI media brand genuinely sound different rather than being
the same account with two logos.
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

logger = logging.getLogger("jarvis.media.script")

SPEC = AgentSpec(
    capability="media.script",
    name="Script and story",
    description=(
        "Turns a verified brief and a strategy into an original script in the "
        "chosen brand's voice, with platform variants, titles and a thumbnail "
        "concept. Every factual line cites the claim it rests on."
    ),
    domain="media",
    supervisor="media.director",
    task_types=("media", "writing"),
    # No PUBLISH. Writing something and putting it in front of the world
    # are different acts, and the second is the one that cannot be undone.
    permissions=frozenset({Permission.READ_MEMORY}),
    model_tiers=(ModelTier.STANDARD, ModelTier.DEEP),
    status=Lifecycle.ACTIVE,
    config={"scopes": ["working", "agent", "project"], "context_chars": 8000},
    max_cost_inr=Decimal("10.00"),
)

_PROMPT = """\
Write the script. Original work only.

{voice}

---

The strategy this must follow:
{strategy}

The verified research it must rest on:
{brief}

---

Rules that are not stylistic:

- Every factual sentence must be traceable to the research above. Put the
  claim it rests on in `cites` for that section. A sentence you cannot
  cite must be removed, not softened.
- Where verification called something misleading, disputed, unsupported
  or lacking context, say so in the script. Do not quietly drop it, and
  do not state it as settled.
- Do not invent numbers, quotes, dates or names. If the research does not
  have it, the script does not say it.
- Write for the platform's length. A Short is not a long-form script cut
  down; it is a different piece of writing.
- The hook must be honest. The script has to deliver exactly what the
  hook promises.

Reply with JSON only:
{{"title_options": ["...", "...", "..."],
  "hook": "...",
  "sections": [{{"beat": "...", "text": "...", "cites": ["..."]}}],
  "cta": "...",
  "description": "...",
  "thumbnail_concept": "...",
  "visual_notes": ["..."],
  "estimated_seconds": 0,
  "uncited_lines": ["any line you could not tie to the research"]}}
"""


def _plain(sections: list) -> str:
    """The script as a person would read it aloud."""
    return "\n\n".join(
        str(s.get("text") or "").strip()
        for s in sections if isinstance(s, dict) and s.get("text")
    ).strip()


async def run(handoff: Handoff, choice) -> _common.AgentResult:
    brief = _common.briefing(handoff)
    strategy = (handoff.inputs or {}).get("strategy") or ""
    brand = _common.brand_of(handoff)

    # The strategy names the brand; the input is the fallback. Getting
    # this wrong produces work in the other property's voice, which is
    # the specific failure the two-brand split exists to prevent.
    if isinstance(strategy, dict) and strategy.get("brand") in brands.BRANDS:
        brand = strategy["brand"]
    elif "\"brand\": \"sagar\"" in brief or "'brand': 'sagar'" in brief:
        brand = brands.PERSONAL

    data, model = await _common.think(
        _PROMPT.format(
            voice=brands.voice(brand),
            strategy=strategy or "(none given -- infer from the brief)",
            brief=brief[:14000] or "(nothing)",
        ),
        "media.script",
    )

    sections = [s for s in (data.get("sections") or []) if isinstance(s, dict)]
    uncited = [str(u) for u in (data.get("uncited_lines") or []) if str(u).strip()]
    titles = [str(t) for t in (data.get("title_options") or []) if str(t).strip()]

    script = {
        "summary": (f"{len(sections)}-section script for {brands.BRANDS[brand]['name']}"
                    + (f", ~{data.get('estimated_seconds')}s" if data.get("estimated_seconds") else "")),
        "brand": brand,
        "title_options": titles,
        "hook": str(data.get("hook") or ""),
        "sections": sections,
        "script": _plain(sections),
        "cta": str(data.get("cta") or ""),
        "description": str(data.get("description") or ""),
        "thumbnail_concept": str(data.get("thumbnail_concept") or ""),
        "visual_notes": [str(v) for v in (data.get("visual_notes") or [])],
        "estimated_seconds": data.get("estimated_seconds"),
    }

    # Confidence falls with uncited lines rather than being asserted. A
    # script whose claims cannot be traced is the exact thing the review
    # step exists to catch, so it should arrive there already flagged.
    cited = sum(1 for s in sections if s.get("cites"))
    grounding = cited / len(sections) if sections else 0
    confidence = round(min(0.85, 0.35 + 0.5 * grounding), 2)

    return _common.result(
        script, confidence=confidence, model=model,
        assumptions=[f"Written in the {brands.BRANDS[brand]['name']} voice"],
        unresolved=[f"Not traceable to the research: {u}" for u in uncited],
        next_action="Send to editorial review.",
    )


async def install() -> None:
    await registry.register(SPEC)
    registry.implement(SPEC.capability, run)
