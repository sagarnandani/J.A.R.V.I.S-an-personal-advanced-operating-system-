"""Running a media job from a topic to something waiting on the owner's desk.

This is the Director's execution half. It does not invoke agents -- the
orchestrator does that, through the one runtime path -- it decides what
graph to run, reads the results, and applies the gates the brief asks for:

* a contradicted claim stops the job before anything is written
* a strategist saying "do not publish" ends it cleanly, not as a failure
* a review that says revise gets one more attempt, then the owner
* a passed script waits for approval and publishes nothing

Everything between those runs with nobody watching, which is the whole
point of section 17 of the brief.

Starting and running are separate calls. Producing a piece takes minutes;
holding an HTTP request open for it means a spinner on a phone and a
proxy timeout, so the caller creates the work, gets an id back, and the
running happens behind it.
"""
import logging
from decimal import Decimal
from uuid import UUID

from app.agents import orchestrator, tasks
from app.media import brands, records, workflow

logger = logging.getLogger("jarvis.media.director")


async def scan(theme: str, requested_by: str, brand: str = brands.AI_MEDIA) -> dict:
    """Look for something worth doing. Commits to nothing.

    One cheap step, no content record: nothing was made, so there is
    nothing to record. What comes back is a queue to choose from.
    """
    return await orchestrator.run(
        f"Scout opportunities: {theme}", requested_by,
        steps=workflow.opportunity_scan(theme, brand),
    )


async def begin(
    topic: str, requested_by: str, brand: str = brands.AI_MEDIA,
    budget_inr: Decimal | None = None,
) -> dict:
    """Create the work and the record of it. Runs nothing yet.

    The record is written before the first model call so a run that dies
    half way leaves something visible. A piece stuck at 'producing' is a
    problem somebody can see; a piece that was never written down is one
    nobody can.
    """
    if brand not in brands.BRANDS:
        brand = brands.AI_MEDIA

    workflow_id = await orchestrator.start(
        f"Media: {topic}", requested_by,
        steps=workflow.produce(topic, brand), budget_inr=budget_inr,
    )
    piece_id = await records.open_piece(workflow_id, topic, brand)

    wf = await tasks.get_workflow(workflow_id)
    rows = await tasks.workflow_tasks(workflow_id)
    if not rows:
        # Planning settled it before any task existed. Recorded as failed
        # rather than left at 'producing' for ever.
        reason = (wf or {}).get("failure_reason") or "No work could be created."
        await records.settle(workflow_id, state="failed", reason=reason)

    return {
        "workflow_id": str(workflow_id),
        "piece_id": str(piece_id) if piece_id else None,
        "topic": topic,
        "brand": brand,
        "state": "producing" if rows else "failed",
        "reason": "" if rows else ((wf or {}).get("failure_reason") or ""),
        "steps": [{"capability": r["capability"], "status": r["status"]} for r in rows],
    }


async def produce(
    topic: str, requested_by: str, brand: str = brands.AI_MEDIA,
    budget_inr: Decimal | None = None,
) -> dict:
    """Take one topic through to a package, or to an honest stop.

    Returns what happened and why, in the owner's terms rather than the
    workflow's -- "the strategist decided against it" is a different
    outcome from "it failed", and the difference matters to whoever reads
    the briefing tomorrow.
    """
    started = await begin(topic, requested_by, brand, budget_inr)
    if started["state"] == "failed":
        return started
    return await run(UUID(started["workflow_id"]), topic, started["brand"])


def _gate_reason(contradicted: list[str]) -> str | None:
    """One sentence, written once, so the gate and the settle agree."""
    if not contradicted:
        return None
    return (
        "verification contradicted a claim the piece would have rested on — "
        + "; ".join(contradicted[:3])
    )


async def _gate(workflow_id: UUID) -> str | None:
    """Asked after every wave: is the rest of this plan still worth running?

    One question, and it is the expensive one to get wrong. If
    verification has contradicted a claim the piece would rest on, then
    the strategy, the script and the review are all about to be built on
    something known to be false -- and a media company that writes around
    a false fact has acquired a correction to publish.

    Stopping here rather than in the settle step is the difference
    between the gate being real and the gate being a label: by the time
    the whole graph has run, the script exists and has been paid for.
    """
    rows = await tasks.workflow_tasks(workflow_id)
    return _gate_reason(workflow.blocking_claims(rows))


async def run(workflow_id: UUID, topic: str, brand: str) -> dict:
    """Advance the graph and settle what came back."""
    await orchestrator.advance(workflow_id, gate=_gate)
    return await _settle(workflow_id, topic, brand)


async def _settle(workflow_id: UUID, topic: str, brand: str, attempt: int = 0) -> dict:
    """Read what came back and decide what happens next."""
    rows = await tasks.workflow_tasks(workflow_id)

    contradicted = workflow.blocking_claims(rows)
    if contradicted:
        # Before anything was written, which is the point of checking
        # first. Writing around a false fact is how a media company
        # acquires a correction to publish.
        reason = "Stopped: " + (_gate_reason(contradicted) or "")
        await tasks.set_workflow_status(workflow_id, "failed", failure=reason)
        return await _outcome(
            workflow_id, "stopped", reason, rows, contradicted=contradicted
        )

    declined = workflow.strategy_declined(rows)
    if declined is not None:
        # Not a failure. Deciding against publishing is the most valuable
        # thing that agent does.
        reason = f"Decided against publishing: {declined}"
        await tasks.set_workflow_status(workflow_id, "completed", failure=None)
        return await _outcome(workflow_id, "declined", reason, rows)

    verdict, review = workflow.verdict_of(rows)

    if verdict == "reject":
        reason = f"Editorial rejected it: {(review or {}).get('why', '')}"
        await tasks.set_workflow_status(workflow_id, "completed", failure=None)
        return await _outcome(workflow_id, "rejected", reason, rows, review=review)

    if verdict == "revise":
        if attempt >= workflow.MAX_REVISIONS:
            reason = (
                "Still not right after a revision. Over to you — the "
                "reviewer's notes are on the task."
            )
            return await _outcome(
                workflow_id, "needs_you", reason, rows, review=review
            )

        await _add(workflow_id, workflow.revision(topic, brand, attempt))
        await orchestrator.advance(workflow_id, gate=_gate)
        return await _settle(workflow_id, topic, brand, attempt + 1)

    if verdict == "pass":
        return await _outcome(
            workflow_id, "ready", "Passed review. Waiting for your approval.",
            rows, review=review,
        )

    row = await tasks.get_workflow(workflow_id)
    return await _outcome(
        workflow_id, (row or {}).get("status", "unknown"),
        (row or {}).get("failure_reason") or "The workflow did not reach a review.",
        rows,
    )


async def _add(workflow_id: UUID, steps: list) -> None:
    """Append steps to a running workflow, wiring their dependencies.

    The orchestrator plans a graph up front; a revision is discovered
    only after a review has spoken. Created here rather than by inventing
    dynamic planning in the orchestrator, which would be a much larger
    change for one known, bounded case.
    """
    from app.db import execute

    by_name: dict[str, UUID] = {}
    for step in steps:
        by_name[step.name] = await tasks.create(
            objective=step.objective, capability=step.capability,
            workflow_id=workflow_id, inputs=step.inputs,
            expected_output=step.expected_output,
        )
    for step in steps:
        deps = [by_name[a] for a in step.after if a in by_name]
        if deps:
            await execute(
                "UPDATE tasks SET depends_on = $2, status = 'blocked' WHERE id = $1",
                by_name[step.name], deps,
            )


async def _outcome(workflow_id, state: str, reason: str, rows: list, **extra) -> dict:
    """Write the record, and return the same thing the caller is told.

    One place, so what the Media tab shows and what the API answered can
    never be two different accounts of the same run.
    """
    spent = sum(Decimal(str(r["spend_inr"] or 0)) for r in rows)
    shadow = sum(Decimal(str(r["shadow_inr"] or 0)) for r in rows)
    package = workflow.package_of(rows)

    try:
        await records.settle(
            workflow_id, state=state, reason=reason,
            package=package, review=extra.get("review"),
            spend_inr=spent, shadow_inr=shadow,
            title=workflow.title_of(package),
        )
    except Exception as exc:  # noqa: BLE001
        # The work happened whether or not the bookkeeping did. Losing
        # the answer because the record failed to write would be the
        # worse of the two outcomes.
        logger.warning("Could not record piece for %s: %s", workflow_id, exc)

    return {
        "workflow_id": str(workflow_id),
        "state": state,
        "reason": reason,
        # Both figures, never added together. On the free tier the real
        # one is zero and true; the shadow one is what makes two pieces
        # of work comparable.
        "spend_inr": float(spent),
        "shadow_inr": float(shadow),
        "package": package,
        "steps": [{"capability": r["capability"], "status": r["status"]} for r in rows],
        **extra,
    }
