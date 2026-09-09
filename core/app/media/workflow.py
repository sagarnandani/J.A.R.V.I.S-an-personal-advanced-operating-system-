"""The Media Director, which is a recipe rather than a manager.

The brief describes a Media Director that coordinates specialists,
manages dependencies and rejects weak work. In this foundation that is
three separate things, and only one of them is an agent:

* deciding what work is required, and in what order -- a Step graph
* rejecting weak work -- `media.review`, an agent that judges and returns
  a verdict, but commands nobody
* reporting without flooding the owner -- the arrival briefing, which
  already reports finished work once

A supervisor that invoked other agents would be a second execution path
alongside `runtime.run_task`, and everything checked in the first would
be optional in the second. Permissions, budgets, cost attribution and
telemetry all hold because there is exactly one door. Keeping the
Director out of the runtime is what preserves that.

The two stopping points are the interesting part. Everything else runs
without the owner.
"""
import logging

from app.agents.schemas import Step
from app.media import brands

logger = logging.getLogger("jarvis.media.workflow")

# How many times a script may come back from review before the owner is
# asked to look. Unbounded, a reviewer and a writer will argue until the
# budget is gone and produce nothing.
MAX_REVISIONS = 1


def opportunity_scan(theme: str, brand: str = brands.AI_MEDIA) -> list[Step]:
    """Just look. Cheap, and nothing downstream commits to anything."""
    return [
        Step(capability="media.scout", objective=theme, name="scout",
             inputs={"brand": brand},
             expected_output="A short ranked queue of opportunities, or none"),
    ]


def produce(topic: str, brand: str = brands.AI_MEDIA) -> list[Step]:
    """Research a topic through to a package waiting for the owner.

    The graph the orchestrator runs. Dependencies are what make it a
    pipeline rather than five unrelated tasks: each step sees the results
    of the ones it names, and nothing else.
    """
    return [
        Step(
            capability="research.web", name="research",
            objective=f"Research thoroughly, preferring primary sources: {topic}",
            expected_output=(
                "What is established, what is contested, what could not be "
                "determined, with the pages relied on"
            ),
        ),
        Step(
            capability="factcheck.claims", name="verify", after=("research",),
            objective=f"Verify the factual claims in the research on: {topic}",
            expected_output="A verdict and evidence for each claim",
        ),
        Step(
            capability="media.strategy", name="strategy",
            after=("research", "verify"),
            objective=f"Decide whether this should become content: {topic}",
            inputs={"brand": brand},
            expected_output="Publish or do not publish, and if so how",
        ),
        Step(
            capability="media.script", name="script", after=("strategy",),
            objective=f"Write the script: {topic}",
            inputs={"brand": brand},
            expected_output="An original script with every factual line cited",
        ),
        Step(
            capability="media.review", name="review", after=("script",),
            objective=f"Review the script for: {topic}",
            inputs={"brand": brand},
            expected_output="Pass, revise with reasons, or reject",
        ),
    ]


def revision(topic: str, brand: str, attempt: int) -> list[Step]:
    """A second pass, after review sent the script back.

    A new pair of steps rather than re-running the old ones: the original
    script and the reasons it was refused both stay in the record, which
    is what makes "how often does this need revising" a measurable thing
    rather than a feeling.
    """
    return [
        Step(
            capability="media.script", name=f"script_v{attempt + 1}",
            objective=f"Rewrite addressing every must-fix from the review: {topic}",
            inputs={"brand": brand},
            expected_output="A revised script that answers each point",
        ),
        Step(
            capability="media.review", name=f"review_v{attempt + 1}",
            after=(f"script_v{attempt + 1}",),
            objective=f"Review the revised script: {topic}",
            inputs={"brand": brand},
            expected_output="Pass, revise, or reject",
        ),
    ]


def verdict_of(rows: list[dict]) -> tuple[str | None, dict | None]:
    """What the most recent review said, and its full result."""
    reviews = [
        r for r in rows
        if r["capability"] == "media.review" and r["status"] == "completed"
        and isinstance(r.get("result"), dict)
    ]
    if not reviews:
        return None, None
    latest = max(reviews, key=lambda r: r["finished_at"] or r["created_at"])
    output = (latest["result"] or {}).get("output") or {}
    return (output.get("verdict") if isinstance(output, dict) else None), output


def blocking_claims(rows: list[dict]) -> list[str]:
    """Claims the verification found contradicted.

    A hard stop rather than something for the editor to catch later.
    Writing around a false fact is how a media company acquires a
    correction to publish, and it is far cheaper to stop here than to
    discover it after a script has been written and reviewed.
    """
    from app.agents.capabilities.factcheck_claims import LOAD_BEARING_FAILURES

    out = []
    for row in rows:
        if row["capability"] != "factcheck.claims" or row["status"] != "completed":
            continue
        output = (row.get("result") or {}).get("output")
        if not isinstance(output, dict):
            continue
        for claim in output.get("claims") or []:
            if isinstance(claim, dict) and claim.get("verdict") in LOAD_BEARING_FAILURES:
                out.append(str(claim.get("claim") or ""))
    return out


def strategy_declined(rows: list[dict]) -> str | None:
    """Whether the strategist said not to make this, and why.

    Not a failure. Deciding against publishing is the most valuable thing
    that agent does, and a workflow that treats it as an error teaches the
    system to stop saying no.
    """
    for row in rows:
        if row["capability"] != "media.strategy" or row["status"] != "completed":
            continue
        output = (row.get("result") or {}).get("output")
        if isinstance(output, dict) and output.get("publish") is False:
            return str(output.get("why") or "No reason given.")
    return None


def package_of(rows: list[dict]) -> dict | None:
    """The most recent script the workflow produced, whole.

    The most recent one, not the first: after a revision there are two,
    and the one the reviewer last looked at is the one that matters. Uses
    the same "latest completed" rule as `verdict_of` so the pair cannot
    disagree about which attempt is being talked about.
    """
    scripts = [
        r for r in rows
        if r["capability"] == "media.script" and r["status"] == "completed"
        and isinstance(r.get("result"), dict)
    ]
    if not scripts:
        return None
    latest = max(scripts, key=lambda r: r["finished_at"] or r["created_at"])
    output = (latest["result"] or {}).get("output")
    return output if isinstance(output, dict) else None


def title_of(package: dict | None) -> str:
    """A name for the piece, for a list the owner reads.

    The first title option, because the script agent returns them best
    first. Falls back to the hook rather than to nothing: an untitled row
    in a list is indistinguishable from a broken one.
    """
    if not isinstance(package, dict):
        return ""
    for option in package.get("title_options") or []:
        if str(option).strip():
            return str(option).strip()[:200]
    return str(package.get("hook") or "").strip()[:200]


def opportunities_of(rows: list[dict]) -> list[dict]:
    """The ranked queue a scan produced, flattened out of the task rows.

    The panel should not have to know that a scan is a workflow with one
    step whose result has an output with an opportunities key. That is
    the sort of knowledge that gets copied into a browser and then goes
    stale the first time the shape changes.
    """
    for row in rows:
        if row["capability"] != "media.scout" or row["status"] != "completed":
            continue
        output = (row.get("result") or {}).get("output")
        if isinstance(output, dict):
            return [o for o in (output.get("opportunities") or []) if isinstance(o, dict)]
    return []
