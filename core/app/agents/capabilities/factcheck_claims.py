"""factcheck.claims -- the capability that makes confidence mean something.

research.web returns an answer and the pages behind it. Nothing in that
loop ever asks whether the answer is *true*: a search that returns four
sources scores 0.85 whether those sources agreed, disagreed, or were four
copies of the same press release. That number is a measure of how well
supported an answer looks, not of whether it holds up.

This capability is the second half. It takes claims -- usually the ones a
research task just produced -- and checks each one against live sources on
its own, returning a verdict per claim with the evidence for it. Two
rules keep it honest:

**A verdict with nothing behind it is not a verdict.** If the check comes
back with no sources, the claim is recorded as unverified whatever the
model said about it. Otherwise the system would launder an unsupported
opinion into a green tick, which is worse than never checking at all.

**Confidence is computed, never asserted.** It falls out of the verdicts
and the number of sources behind them, by the same arithmetic every time,
so two results can actually be compared.

It deliberately does NOT hold READ_MEMORY. A fact-checker shown what the
owner already believes has been given a reason to agree with it, and the
whole value of the check is that it is independent of that.
"""
import asyncio
import json
import logging
import re
from decimal import Decimal

from app.agents import registry
from app.agents.schemas import (
    AgentError,
    AgentResult,
    AgentSpec,
    Handoff,
    Lifecycle,
    ModelTier,
    Permission,
    PermissionDenied,
)
from app.agents.tools import websearch
from app.config import get_settings

logger = logging.getLogger("jarvis.capabilities.factcheck")

SUPPORTED = "supported"          # sources back it
CONTRADICTED = "contradicted"    # sources say otherwise
DISPUTED = "disputed"            # sources do not agree with each other
UNVERIFIED = "unverified"        # nothing found either way

VERDICTS = (SUPPORTED, CONTRADICTED, DISPUTED, UNVERIFIED)

# How many claims one task will check. A ten-claim research answer would
# otherwise mean ten searches, and on a free tier that is the difference
# between a check and a rate limit. The caller can raise it deliberately.
DEFAULT_MAX_CLAIMS = 5

SPEC = AgentSpec(
    capability="factcheck.claims",
    name="Fact check",
    description=(
        "Checks stated claims against live sources one at a time and "
        "returns a verdict and the evidence for each."
    ),
    domain="research",
    task_types=("general", "factcheck", "verification"),
    tools=("websearch",),
    # NETWORK only. No READ_MEMORY on purpose -- see the module docstring.
    # A checker that knows what the owner wants to be true is not a checker.
    permissions=frozenset({Permission.NETWORK}),
    # Cheap is enough to split prose into claims; the judgement happens in
    # grounded search, not in the model's own recollection.
    model_tiers=(ModelTier.CHEAP, ModelTier.STANDARD),
    status=Lifecycle.ACTIVE,
    # Working scope only, and roomy: the thing being checked arrives here,
    # as the result of the task this one depends on.
    config={"scopes": ["working"], "context_chars": 4000},
    max_cost_inr=Decimal("8.00"),
)

_SPLIT_PROMPT = """\
Split the material below into separate factual claims that could be
checked against a source.

Take only statements of fact -- something that is either true or false.
Skip opinions, recommendations, questions, and anything hedged so far
("may", "some believe") that there is nothing to check.

Write each claim so it stands alone, with its own subject and any date or
figure it depends on. "Revenue grew 12% in 2024", not "it grew 12%".

At most {limit} claims, most significant first.

Material:
{material}

Reply with JSON only, no other text:
{{"claims": ["...", "..."]}}
"""

_CHECK_PROMPT = """\
Check the following claim against current sources.

Reply in exactly this form:
VERDICT: SUPPORTED or CONTRADICTED or DISPUTED or UNVERIFIED
WHY: one sentence, naming what the sources actually say

Use SUPPORTED only if sources state it. Use CONTRADICTED if sources state
the opposite. Use DISPUTED if sources genuinely disagree with each other.
Use UNVERIFIED if you cannot find sources addressing it -- absence of
evidence is not evidence, and guessing here is the one unforgivable
answer.

Claim: {claim}
"""


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower().rstrip(".")


def _parse_claims(raw: str) -> list[str]:
    """Read the split model's JSON, forgivingly and without ever raising.

    Same rule as fact extraction: models fence their JSON, preface it, or
    return prose on a bad day. Unparseable means no claims, which the
    caller reports honestly -- it must never take the whole task down.
    """
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]

    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        logger.warning("Claim splitting did not return JSON; nothing to check.")
        return []

    if not isinstance(data, dict):
        return []
    return [c.strip() for c in (data.get("claims") or [])
            if isinstance(c, str) and c.strip()]


def _dedupe(claims: list[str], limit: int) -> list[str]:
    """The same claim twice is a second search bought for nothing."""
    seen: set[str] = set()
    out: list[str] = []
    for claim in claims:
        key = _normalise(claim)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(claim.strip())
        if len(out) >= limit:
            break
    return out


def _read_verdict(text: str, sources: list[str]) -> str:
    """What the check came back as -- with the rule that outranks it.

    A verdict resting on no sources is downgraded to unverified however
    firmly it was stated. This is the whole point of the capability: a
    model asserting "SUPPORTED" from memory, with nothing to cite, is the
    exact failure a fact-checker exists to catch, and letting it through
    would turn an unchecked opinion into a green tick.
    """
    lowered = (text or "").lower()
    match = re.search(r"verdict\W{0,4}(" + "|".join(VERDICTS) + r")", lowered)
    if match:
        verdict = match.group(1)
    else:
        # No stated verdict line. Take the first verdict word it used, and
        # fall back to unverified rather than guessing generously.
        found = [(lowered.find(v), v) for v in VERDICTS if lowered.find(v) != -1]
        verdict = min(found)[1] if found else UNVERIFIED

    if not sources and verdict != UNVERIFIED:
        return UNVERIFIED
    return verdict


def _claim_confidence(verdict: str, sources: list[str]) -> float:
    """How good this particular check was, from what it rested on.

    Mechanical on purpose, like research.web's. A firm verdict with four
    independent pages behind it is worth more than the same words with
    one, and neither is ever certainty -- the ceiling is 0.9 because
    agreeing sources are still only agreeing sources.
    """
    if verdict == UNVERIFIED:
        return 0.2
    support = min(len(sources), 4) / 4          # 0.25 .. 1.0
    firmness = 0.5 if verdict == DISPUTED else 1.0
    return round(0.3 + 0.6 * support * firmness, 2)


def _overall(checked: list[dict]) -> float:
    """One number for the task, from the checks it actually managed."""
    if not checked:
        return 0.2
    return round(sum(c["confidence"] for c in checked) / len(checked), 2)


def _summary(checked: list[dict]) -> str:
    """The line a human reads first, and the one a later agent inherits."""
    if not checked:
        return "No checkable claims were found, so nothing has been verified."

    counts = {v: sum(1 for c in checked if c["verdict"] == v) for v in VERDICTS}
    parts = [f"{counts[v]} {v}" for v in VERDICTS if counts[v]]
    head = f"Checked {len(checked)} claim(s): {', '.join(parts)}."

    lines = [head, ""]
    for c in checked:
        lines.append(f"[{c['verdict'].upper()}] {c['claim']}")
        if c["why"]:
            lines.append(f"    {c['why']}")
        for source in c["sources"][:3]:
            lines.append(f"    - {source}")
    return "\n".join(lines)


def _material(handoff: Handoff) -> str:
    """What this task was actually asked to check.

    The context comes first because in a real workflow it holds the
    result of the research task this one depends on -- the objective is
    usually just "check the findings".
    """
    parts = [handoff.context, str((handoff.inputs or {}).get("material", "") or "")]
    body = "\n\n".join(p for p in parts if p.strip())
    return body or handoff.objective


async def _split(handoff: Handoff, limit: int) -> tuple[list[str], int, int, str]:
    """Turn prose into separate checkable claims.

    Given claims explicitly, this does not run at all -- a caller that
    already knows what it wants checked should not pay for a model call to
    be told the same thing back.
    """
    given = (handoff.inputs or {}).get("claims")
    if isinstance(given, str):
        given = [given]
    if isinstance(given, list) and given:
        return _dedupe([str(c) for c in given], limit), 0, 0, ""

    from app.llm import get_provider

    material = _material(handoff)
    if not material.strip():
        return [], 0, 0, ""

    provider = get_provider(get_settings())
    result = await provider.complete(
        _SPLIT_PROMPT.format(limit=limit, material=material[:8000])
    )
    return (
        _dedupe(_parse_claims(result.text), limit),
        result.input_tokens,
        result.output_tokens,
        result.model,
    )


async def _check(claim: str, handoff: Handoff, settings) -> dict:
    """One claim, one grounded search, one verdict.

    A search failure here is recorded against the claim rather than
    thrown: five claims where one lookup failed is a useful result with a
    gap in it, and losing the other four to that gap helps nobody. The
    caller re-raises only if every single one failed.
    """
    try:
        found = await websearch.search(
            _CHECK_PROMPT.format(claim=claim),
            granted=handoff.permissions,
            settings=settings,
            max_sources=int((handoff.constraints or {}).get("max_sources", 5)),
        )
    except PermissionDenied:
        # Not a gap in one claim -- this agent may not reach the web at
        # all, and every other claim will fail the same way. It goes
        # straight up so the runtime records a refusal rather than a
        # failure, which are different things and are read differently.
        raise
    except AgentError as exc:
        return {"claim": claim, "verdict": UNVERIFIED, "confidence": 0.2,
                "why": f"The check could not run: {exc}", "sources": [],
                "error": str(exc), "tokens_in": 0, "tokens_out": 0,
                "model": "", "retryable": exc.retryable}

    verdict = _read_verdict(found.answer, found.sources)
    why = ""
    match = re.search(r"why\W{0,4}(.+)", found.answer or "", re.I)
    if match:
        why = match.group(1).strip().splitlines()[0][:400]

    return {"claim": claim, "verdict": verdict,
            "confidence": _claim_confidence(verdict, found.sources),
            "why": why, "sources": found.sources, "error": None,
            "tokens_in": found.tokens_in, "tokens_out": found.tokens_out,
            "model": found.model, "retryable": False}


async def run(handoff: Handoff, choice) -> AgentResult:
    settings = get_settings()
    limit = int((handoff.constraints or {}).get("max_claims", DEFAULT_MAX_CLAIMS))

    claims, tokens_in, tokens_out, split_model = await _split(handoff, limit)

    if not claims:
        return AgentResult(
            output={"summary": _summary([]), "claims": []},
            confidence=_overall([]),
            unresolved=["Nothing in the material was a checkable factual claim."],
            next_action="Give the claims to check explicitly, in inputs['claims'].",
            model_used=split_model or None,
            tokens_in=tokens_in, tokens_out=tokens_out,
            cost_inr=Decimal(0),   # priced by the runtime, from one price list
        )

    # In parallel: the claims are independent, and the owner waits on the
    # slowest one either way. Bounded by `limit`, which is what keeps this
    # from turning into a burst that trips a free-tier rate limit.
    checked = await asyncio.gather(
        *(_check(claim, handoff, settings) for claim in claims)
    )

    failures = [c for c in checked if c["error"]]
    if len(failures) == len(checked):
        # Nothing was checked at all. That is a failed task, not a result
        # full of "unverified" -- reporting the latter would look like the
        # claims were examined and found wanting.
        raise AgentError(
            f"No claim could be checked: {failures[0]['error']}",
            retryable=any(f["retryable"] for f in failures),
        )

    evidence: list[str] = []
    for c in checked:
        for source in c["sources"]:
            if source not in evidence:
                evidence.append(source)

    unresolved = [
        f"{c['claim']} — {c['why'] or 'no sources addressed it'}"
        for c in checked if c["verdict"] == UNVERIFIED
    ]
    wrong = [c for c in checked if c["verdict"] == CONTRADICTED]

    return AgentResult(
        output={"summary": _summary(list(checked)),
                "claims": [{k: c[k] for k in
                            ("claim", "verdict", "confidence", "why", "sources")}
                           for c in checked]},
        confidence=_overall(list(checked)),
        evidence=evidence,
        assumptions=(
            [f"Claims split by {split_model}"] if split_model else
            ["Claims were supplied by the caller, not extracted"]
        ),
        unresolved=unresolved,
        next_action=(
            f"{len(wrong)} claim(s) were contradicted by sources — do not act "
            "on the material until they are corrected."
            if wrong else
            "Unverified claims still need a source before they are relied on."
            if unresolved else "Every claim was checked against sources."
        ),
        model_used=next((c["model"] for c in checked if c["model"]), None),
        tokens_in=tokens_in + sum(c["tokens_in"] for c in checked),
        tokens_out=tokens_out + sum(c["tokens_out"] for c in checked),
        cost_inr=Decimal(0),   # priced by the runtime, from one price list
    )


async def install() -> None:
    await registry.register(SPEC)
    registry.implement(SPEC.capability, run)
