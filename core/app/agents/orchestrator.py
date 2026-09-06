"""Objective in, task graph out, results back.

Deliberately not an autonomous planner yet. The brief asks for the
architecture that lets orchestration intelligence grow, not for a system
that plans its own work on day one -- and a planner that can invent
arbitrary task graphs before permissions, budgets and telemetry are
proven is the least safe thing to build first.

So planning is a seam: `plan()` turns an objective into tasks, and today
it does that from an explicit recipe or a single delegation. Replacing
its body with a model-driven planner later changes nothing below it,
because everything below works from the task rows rather than from
whatever produced them.

Execution runs the graph in waves: everything currently runnable goes at
once, then the graph is asked again. That gives concurrency and
dependency ordering without a workflow engine, and the "ask again" is a
database query, so it is correct even if two of these run at once.
"""
import asyncio
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from app.agents import registry, runtime, tasks, telemetry
from app.agents.schemas import TaskStatus


@dataclass
class Step:
    """One intended piece of work, before it becomes a task row."""

    capability: str
    objective: str
    inputs: dict | None = None
    expected_output: str = ""
    constraints: dict | None = None
    after: tuple[str, ...] = ()   # names of steps this one needs
    name: str = ""
    budget_inr: Decimal | None = None


async def plan(objective: str, steps: list[Step] | None = None) -> list[Step]:
    """Decide what work an objective requires.

    One step today when given nothing: hand the objective to whichever
    capability can take it. The signature is the important part -- it is
    where a real planner lands, and it returns Steps rather than acting,
    so a plan can be inspected and approved before anything runs.
    """
    if steps:
        return steps

    candidates = await registry.find(task_type="general")
    if not candidates:
        return []
    return [Step(capability=candidates[0].capability, objective=objective,
                 name="direct")]


async def start(
    objective: str,
    requested_by: str,
    steps: list[Step] | None = None,
    budget_inr: Decimal | None = None,
) -> UUID:
    """Create the workflow and its task graph. Nothing runs yet."""
    workflow_id = await tasks.create_workflow(objective, requested_by, budget_inr)
    resolved = await plan(objective, steps)

    if not resolved:
        await tasks.set_workflow_status(
            workflow_id, "failed",
            failure="No registered capability can take this objective.",
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
                "capabilities": [s.capability for s in resolved]},
    )
    return workflow_id


async def advance(workflow_id: UUID, max_waves: int = 20) -> dict:
    """Run the graph until nothing more can run.

    Bounded by waves, not by time. An unbounded loop here is how a
    dependency cycle or a task that re-queues itself turns into a process
    that never returns, and the bound makes that visible as a stuck
    workflow rather than a hung server.
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

    return await _settle(workflow_id, waves)


async def _settle(workflow_id: UUID, waves: int) -> dict:
    """Decide what the workflow as a whole amounts to."""
    rows = await tasks.workflow_tasks(workflow_id)
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
    return {"workflow_id": str(workflow_id), "status": status,
            "tasks": counts, "waves": waves, "outputs": outputs}


async def run(
    objective: str,
    requested_by: str,
    steps: list[Step] | None = None,
    budget_inr: Decimal | None = None,
) -> dict:
    """Plan and execute. The ordinary entry point."""
    workflow_id = await start(objective, requested_by, steps, budget_inr)
    return await advance(workflow_id)
