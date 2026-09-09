"""Objective in, task graph out, results back.

Planning and execution are separate, and this module is the execution
half. Where the steps came from -- a caller who wrote them out, or
`planner` working them out from the registry -- makes no difference here,
because everything below works from the task rows rather than from
whatever produced them. That separation is what let the planner arrive
last, after permissions, budgets and telemetry were proven, rather than
first, when a component that can invent arbitrary task graphs would have
had nothing underneath it to be bounded by.

Explicit steps always win. A caller who names the steps has decided
something on purpose, and a proposal must never override that.

Execution runs the graph in waves: everything currently runnable goes at
once, then the graph is asked again. That gives concurrency and
dependency ordering without a workflow engine, and the "ask again" is a
database query, so it is correct even if two of these run at once.
"""
import asyncio
from collections.abc import Awaitable, Callable
from decimal import Decimal
from uuid import UUID

from app.agents import planner, runtime, tasks, telemetry
from app.agents.schemas import Step, TaskStatus

__all__ = ["Gate", "Step", "advance", "plan", "propose", "run", "start"]

# Asked between waves. Returns a reason to stop, or None to carry on.
Gate = Callable[[UUID], Awaitable[str | None]]


async def propose(objective: str, max_steps: int | None = None) -> planner.Plan:
    """What JARVIS would do about this. Creates nothing, runs nothing.

    The review seam. A plan is a proposal until somebody starts it, and
    reading one costs a single cheap model call rather than the whole
    workflow.
    """
    return await planner.propose(objective, max_steps=max_steps)


async def plan(objective: str, steps: list[Step] | None = None) -> list[Step]:
    """Decide what work an objective requires.

    Given steps, those are the plan -- an explicit recipe always wins over
    a proposed one, because somebody wrote it on purpose. Given none,
    `planner` works one out from the registry.

    Still returns Steps rather than acting, which is what keeps planning
    reviewable: nothing here creates a task or spends anything beyond the
    one call that produced the proposal.
    """
    if steps:
        return steps

    return (await planner.propose(objective)).steps


async def start(
    objective: str,
    requested_by: str,
    steps: list[Step] | None = None,
    budget_inr: Decimal | None = None,
) -> UUID:
    """Create the workflow and its task graph. Nothing runs yet."""
    workflow_id = await tasks.create_workflow(objective, requested_by, budget_inr)

    if steps:
        proposal = planner.Plan(steps=steps, source="given",
                                reasoning="Steps were specified by the caller.")
    else:
        proposal = await planner.propose(objective)
    resolved = proposal.steps

    # Recorded before anything is created, so a plan that produced nothing
    # is as readable afterwards as one that produced work.
    await telemetry.record(
        "plan_proposed", workflow_id=workflow_id, detail=proposal.as_detail()
    )

    if not resolved:
        await tasks.set_workflow_status(
            workflow_id, "failed",
            failure=(
                proposal.reasoning
                or "No registered capability can take this objective."
            ),
        )
        return workflow_id

    # Two passes: create every task, then wire dependencies. A step can
    # name a later step's output, and the ids do not exist until created.
    by_name: dict[str, UUID] = {}
    for i, step in enumerate(resolved):
        name = step.name or f"step{i}"
        by_name[name] = await tasks.create(
            objective=step.objective, capability=step.capability,
            workflow_id=workflow_id, inputs=step.inputs,
            expected_output=step.expected_output, constraints=step.constraints,
            budget_cost=step.budget_inr,
        )

    from app.db import execute

    for i, step in enumerate(resolved):
        if not step.after:
            continue
        name = step.name or f"step{i}"
        deps = [by_name[a] for a in step.after if a in by_name]
        if deps:
            await execute(
                "UPDATE tasks SET depends_on = $2, status = 'blocked' WHERE id = $1",
                by_name[name], deps,
            )

    await telemetry.record(
        "workflow_planned", workflow_id=workflow_id,
        detail={"objective": objective, "steps": len(resolved),
                "capabilities": [s.capability for s in resolved],
                "planned_by": proposal.source},
    )
    return workflow_id


async def advance(
    workflow_id: UUID, max_waves: int = 20, gate: Gate | None = None
) -> dict:
    """Run the graph until nothing more can run, or a gate says stop.

    Bounded by waves, not by time. An unbounded loop here is how a
    dependency cycle or a task that re-queues itself turns into a process
    that never returns, and the bound makes that visible as a stuck
    workflow rather than a hung server.

    `gate` is asked between waves whether the rest of the graph should
    still happen. It exists because some workflows have a step whose
    answer invalidates everything after it -- verification finding that a
    claim is false, say -- and letting the remaining steps run anyway
    means paying for work built on something known to be wrong. A gate
    decides, it never runs anything, so there is still exactly one path
    by which an agent executes.
    """
    waves = 0
    while waves < max_waves:
        ready = await tasks.runnable(workflow_id)
        if not ready:
            break
        waves += 1

        # Everything ready goes at once. Independent work has no reason to
        # queue behind unrelated work.
        await asyncio.gather(
            *(runtime.run_task(t["id"]) for t in ready), return_exceptions=True
        )

        if gate is not None:
            stop = await gate(workflow_id)
            if stop:
                # Cancelled, not failed. Nothing broke: what was learned
                # in this wave made the rest of the plan wrong.
                await tasks.cancel_workflow(workflow_id, stop)
                await telemetry.record(
                    "workflow_gated", workflow_id=workflow_id,
                    detail={"reason": stop, "wave": waves},
                )
                break

    return await _settle(workflow_id, waves)


async def _settle(workflow_id: UUID, waves: int) -> dict:
    """Decide what the workflow as a whole amounts to."""
    rows = await tasks.workflow_tasks(workflow_id)

    if not rows:
        # No tasks at all, so there is nothing here to derive a status
        # from -- the workflow's fate was already decided when planning
        # produced nothing, and it came with a reason. Recomputing here
        # would replace that reason with "running", which is both wrong
        # and permanent: nothing will ever move it again.
        wf = await tasks.get_workflow(workflow_id)
        status = (wf or {}).get("status", "failed")
        await telemetry.record(
            "workflow_settled", workflow_id=workflow_id,
            detail={"status": status, "waves": waves, "tasks": {},
                    "why": "the workflow has no tasks"},
        )
        return {"workflow_id": str(workflow_id), "status": status,
                "tasks": {}, "waves": waves, "outputs": {}}

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    waiting = counts.get(TaskStatus.WAITING_APPROVAL.value, 0)
    failed = counts.get(TaskStatus.FAILED.value, 0)
    done = counts.get(TaskStatus.COMPLETED.value, 0)

    if waiting:
        status = "waiting_approval"
    elif failed:
        status = "failed"
    elif done == len(rows) and rows:
        status = "completed"
    else:
        status = "running"

    outputs = {
        r["capability"]: (r["result"] or {}).get("output")
        for r in rows
        if r["status"] == TaskStatus.COMPLETED.value and r["result"]
    }
    await tasks.set_workflow_status(
        workflow_id, status,
        result={"outputs": outputs} if outputs else None,
        failure=next((r["failure_reason"] for r in rows
                      if r["status"] == TaskStatus.FAILED.value), None),
    )
    await telemetry.record(
        "workflow_settled", workflow_id=workflow_id,
        detail={"status": status, "waves": waves, "tasks": counts},
    )

    # What the work found becomes something JARVIS remembers. Without
    # this a research run answers the question and is forgotten by the
    # next message -- the exact failure long-term memory exists to stop --
    # and it applies however the work was started, from the Tasks tab or
    # from a sentence in conversation.
    if status == "completed" and outputs:
        wf = await tasks.get_workflow(workflow_id)
        await _remember(wf, outputs)

    return {"workflow_id": str(workflow_id), "status": status,
            "tasks": counts, "waves": waves, "outputs": outputs}


def _readable(outputs: dict) -> str:
    """The workflow's result as something a person, or a later prompt, reads.

    A structured output hands over its own summary; anything else goes as
    it stands. The last step speaks last, because in a chain it is the one
    that had everything before it.
    """
    parts = []
    for capability, output in outputs.items():
        if isinstance(output, dict):
            output = output.get("summary") or output
        parts.append(f"{capability}: {output}")
    return "\n\n".join(str(p) for p in parts)


async def _remember(wf, outputs: dict) -> None:
    from app import offer

    if not wf:
        return
    try:
        await offer.remember_outcome(wf["objective"], _readable(outputs))
    except Exception:  # noqa: BLE001 - remembering must not fail the work
        pass


async def run(
    objective: str,
    requested_by: str,
    steps: list[Step] | None = None,
    budget_inr: Decimal | None = None,
    gate: Gate | None = None,
) -> dict:
    """Plan and execute. The ordinary entry point."""
    workflow_id = await start(objective, requested_by, steps, budget_inr)
    return await advance(workflow_id, gate=gate)
