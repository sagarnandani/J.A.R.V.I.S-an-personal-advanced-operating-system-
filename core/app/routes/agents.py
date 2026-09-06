"""A thin window onto the Agent Foundation.

Deliberately small. The brief says not to turn JARVIS into an engineering
dashboard, so this exposes what is needed to run and inspect work and
nothing more. The owner still talks to JARVIS; this is for the machinery
behind it.
"""
import asyncio
import logging
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.agents import orchestrator, registry, tasks, telemetry
from app.auth import CurrentUser, get_current_user

router = APIRouter()
logger = logging.getLogger("jarvis.routes.agents")

# Live background runs, held so the event loop does not collect them.
# asyncio keeps only a weak reference to a bare create_task, so a
# fire-and-forget workflow can vanish mid-step -- which looks exactly like
# a task that hung.
_RUNNING: set[asyncio.Task] = set()


def _run_detached(workflow_id: UUID) -> None:
    """Let the work happen after the response has gone.

    A research-then-check workflow takes half a minute or more. Holding
    the HTTP request open for it means a spinner on a phone, a proxy
    timeout, and no way to close the tab and come back -- so the request
    returns as soon as the tasks exist, and the panel polls.
    """
    task = asyncio.create_task(orchestrator.advance(workflow_id))
    _RUNNING.add(task)

    def _done(finished: asyncio.Task) -> None:
        _RUNNING.discard(finished)
        if not finished.cancelled() and finished.exception():
            # The workflow's own rows already record what happened to each
            # task; this is for the failure that escaped all of them.
            logger.error("Workflow %s ended in an exception: %s",
                         workflow_id, finished.exception())

    task.add_done_callback(_done)


class StepIn(BaseModel):
    capability: str
    objective: str
    name: str = ""
    after: list[str] = []
    inputs: dict = {}
    expected_output: str = ""
    constraints: dict = {}


class WorkflowIn(BaseModel):
    objective: str
    steps: list[StepIn] = []
    budget_inr: Decimal | None = None


class PlanIn(BaseModel):
    objective: str
    max_steps: int | None = None


@router.get("/v1/agents", include_in_schema=False)
async def list_agents(
    task_type: str | None = None,
    routable_only: bool = True,
    user: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    found = await registry.find(task_type=task_type, routable_only=routable_only)
    return [
        {
            "capability": s.capability, "version": s.version, "name": s.name,
            "description": s.description, "domain": s.domain,
            "status": s.status.value, "task_types": list(s.task_types),
            "permissions": sorted(p.value for p in s.permissions),
            "model_tiers": [t.value for t in s.model_tiers],
        }
        for s in found
    ]


@router.post("/v1/workflows", include_in_schema=False)
async def start_workflow(
    body: WorkflowIn, user: CurrentUser = Depends(get_current_user)
) -> dict:
    """Create the task graph, start it, and answer straight away.

    The work continues after the response. Poll the workflow to watch it.
    """
    steps = [
        orchestrator.Step(
            capability=s.capability, objective=s.objective, name=s.name,
            after=tuple(s.after), inputs=s.inputs,
            expected_output=s.expected_output, constraints=s.constraints,
        )
        for s in body.steps
    ] or None

    workflow_id = await orchestrator.start(
        body.objective, f"user:{user.email or user.uid}", steps, body.budget_inr
    )
    rows = await tasks.workflow_tasks(workflow_id)
    wf = await tasks.get_workflow(workflow_id)

    # Planning can settle a workflow before any task exists -- nothing
    # registered could take the objective. Starting a runner for that
    # would only rediscover it.
    if rows:
        _run_detached(workflow_id)

    return {
        "workflow_id": str(workflow_id),
        "status": "running" if rows else (wf or {}).get("status", "failed"),
        "failure_reason": (wf or {}).get("failure_reason"),
        "tasks": rows,
    }


@router.get("/v1/workflows", include_in_schema=False)
async def list_workflows(
    limit: int = 10, user: CurrentUser = Depends(get_current_user)
) -> list[dict]:
    return await tasks.recent_workflows(min(max(limit, 1), 50))


@router.post("/v1/plans", include_in_schema=False)
async def propose_plan(
    body: PlanIn, user: CurrentUser = Depends(get_current_user)
) -> dict:
    """What JARVIS would do about this, without doing any of it.

    Read before run. Starting a workflow spends money on every step;
    asking what the steps would be spends one cheap call, and the answer
    includes what the planner asked for and was refused.
    """
    plan = await orchestrator.propose(body.objective, body.max_steps)
    return plan.as_detail()


@router.get("/v1/workflows/{workflow_id}", include_in_schema=False)
async def workflow(
    workflow_id: UUID, user: CurrentUser = Depends(get_current_user)
) -> dict:
    wf = await tasks.get_workflow(workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail="No such workflow.")
    return {
        "workflow": wf,
        "tasks": await tasks.workflow_tasks(workflow_id),
        # The audit trail: why each agent ran, what it was told, what it cost.
        "trace": await telemetry.trace(workflow_id),
    }
