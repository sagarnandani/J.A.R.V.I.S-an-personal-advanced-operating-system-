"""media.review -- the independent gate.

The only agent in the chain whose job is to disagree, and the only one
with no permissions at all. Give it the network and it starts researching
instead of judging; give it memory and it starts agreeing with the house
view. It sees the script, the brief and the verdicts, and nothing else.

It is also the only capability routed to the deep tier. A weak gate is
worse than no gate, because it produces a stamp.

Its verdict is one of three, and `revise` is the important one: work that
is nearly right should come back with reasons, not be rejected outright
or waved through. The workflow bounds that loop, because a reviewer and a
writer left alone will argue until the budget is gone.
"""
import logging
from decimal import Decimal

from app.agents import registry
from app.agents.schemas import (
    AgentSpec,
    Handoff,
    Lifecycle,
    ModelTier,
)
from app.media import _common, brands

logger = logging.getLogger("jarvis.media.review")

PASS, REVISE, REJECT = "pass", "revise", "reject"

CRITERIA = (
    "factual_accuracy", "claims_match_evidence", "originality", "clarity",
    "hook_quality", "hype", "platform_fit", "brand_voice",
    "reputational_risk", "copyright",
)

SPEC = AgentSpec(
    capability="media.review",
    name="Editorial review",
    description=(
        "Independently judges a script against the evidence it claims to rest "
        "on and the brand it claims to be written for. Passes, sends back for "
        "revision with reasons, or rejects."
    ),
    domain="media",
    supervisor="media.director",
    task_types=("media", "review"),
    # Nothing. Independence is the whole value.
    permissions=frozenset(),
    # Deep only. This is the last thing between weak work and the owner.
    model_tiers=(ModelTier.DEEP, ModelTier.STANDARD),
    status=Lifecycle.ACTIVE,
    config={"scopes": ["working"], "context_chars": 14000},
    max_cost_inr=Decimal("10.00"),
)

_PROMPT = """\
You are the editor. You did not write this and you are not trying to save
it. Your job is to decide whether it should reach the owner.

The brand it claims to be written for:
{voice}

---

Everything the workflow produced -- research, verification, strategy and
the script:
{package}

---

Judge each criterion 0-1, where 1 is sound:

{criteria}

Then one verdict:
- "pass"   -- fit to go to the owner
- "revise" -- nearly right; say exactly what to change
- "reject" -- should not exist in this form

Reject rather than revise when the problem is the premise: the evidence
does not support the angle, or the piece has no honest reason to exist.

The things that must always fail:
- a factual line that the research does not support
- a claim stated as settled that verification called disputed, misleading
  or unsupported
- a hook the script does not deliver on
- guru language, manufactured urgency, or invented figures
- a voice that reads as the other property

Reply with JSON only:
{{"verdict": "pass" or "revise" or "reject",
  "scores": {{"factual_accuracy": 0.0, ...}},
  "must_fix": ["..."], "should_fix": ["..."],
  "why": "one or two sentences",
  "unsupported_claims": ["..."]}}
"""


def _weakest(scores: dict) -> list[str]:
    """The criteria that dragged it down, named for the revision pass."""
    out = []
    for name in CRITERIA:
        try:
            value = float(scores.get(name, 1))
        except (TypeError, ValueError):
            continue
        if value < 0.6:
            out.append(f"{name.replace('_', ' ')} ({value:.2f})")
    return out


async def run(handoff: Handoff, choice) -> _common.AgentResult:
    package = _common.briefing(handoff)
    brand = _common.brand_of(handoff)
    if not package:
        return _common.result(
            {"summary": "Nothing to review.", "verdict": REJECT,
             "why": "No script reached this step."},
            confidence=0.3, model=None,
            next_action="Nothing to send to the owner.",
        )

    data, model = await _common.think(
        _PROMPT.format(
            voice=brands.voice(brand),
            package=package[:16000],
            criteria="\n".join(f"- {c}" for c in CRITERIA),
        ),
        "media.review",
    )

    verdict = str(data.get("verdict") or "").lower()
    if verdict not in {PASS, REVISE, REJECT}:
        # An unreadable verdict is not a pass. The gate fails closed.
        verdict = REVISE

    scores = data.get("scores") if isinstance(data.get("scores"), dict) else {}
    must_fix = [str(m) for m in (data.get("must_fix") or []) if str(m).strip()]
    unsupported = [str(u) for u in (data.get("unsupported_claims") or []) if str(u).strip()]

    # A pass with unsupported claims or a must-fix is a contradiction, and
    # the safe reading is the stricter one. Left alone, this is exactly
    # how weak work acquires a stamp.
    if verdict == PASS and (must_fix or unsupported):
        verdict = REVISE

    return _common.result(
        {"summary": f"Review: {verdict}. {data.get('why', '')}",
         "verdict": verdict, "why": str(data.get("why") or ""),
         "scores": scores, "must_fix": must_fix,
         "should_fix": [str(s) for s in (data.get("should_fix") or [])],
         "unsupported_claims": unsupported,
         "weakest": _weakest(scores)},
        # High when it passed something clean, lower when it is sending
        # work back -- a revise verdict is a judgement call in a way a
        # clean pass is not.
        confidence=0.8 if verdict == PASS and not unsupported else 0.65,
        model=model,
        unresolved=unsupported,
        next_action=("Ready for the owner." if verdict == PASS
                     else "Revise and re-review." if verdict == REVISE
                     else "Do not make this."),
    )


async def install() -> None:
    await registry.register(SPEC)
    registry.implement(SPEC.capability, run)
