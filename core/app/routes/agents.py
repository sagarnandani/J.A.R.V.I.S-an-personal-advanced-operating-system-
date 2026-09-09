"""A thin window onto the Agent Foundation.

Deliberately small. The brief says not to turn JARVIS into an engineering
dashboard, so this exposes what is needed to run and inspect work and
nothing more. The owner still talks to JARVIS; this is for the machinery
behind it.
"""
import asyncio
import logging
import secrets
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from app import scheduler, system_control
from app.agents import approvals, orchestrator, registry, tasks, telemetry
from app.auth import CurrentUser, get_current_user
from app.config import get_settings

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


class DecisionIn(BaseModel):
    reason: str | None = None


class ScheduleIn(BaseModel):
    objective: str
    hour: int
    minute: int = 0
    # ISO weekdays, 1=Monday..7=Sunday. Empty means every day.
    days: list[int] = []
    max_per_day: int = 2


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


@router.get("/v1/org", include_in_schema=False)
async def org_tree(user: CurrentUser = Depends(get_current_user)) -> dict:
    """The whole organisation, generated from the registry.

    Nothing here is drawn by hand. An agent registered, reassigned,
    degraded or retired changes this answer on the next request, which is
    the only way a chart of a living system stays true.
    """
    from app.agents import org

    return await org.tree()


@router.get("/v1/org/{node_id}", include_in_schema=False)
async def org_node(
    node_id: str,
    user: CurrentUser = Depends(get_current_user),
    settings=Depends(get_settings),
) -> dict:
    """One node in full: who it is, what it may do, how it has gone."""
    from app.agents import org

    found = await org.detail(node_id, settings)
    if found is None:
        raise HTTPException(status_code=404, detail="No such agent.")
    return found


@router.post("/v1/workflows", include_in_schema=False)
async def start_workflow(
    body: WorkflowIn, user: CurrentUser = Depends(get_current_user)
) -> dict:
    """Create the task graph, start it, and answer straight away.

    The work continues after the response. Poll the workflow to watch it.
    """
    await system_control.refuse_if_stopped()
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
    await system_control.refuse_if_stopped()
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


# --- work that happens without being asked ---------------------------------

@router.get("/v1/schedules", include_in_schema=False)
async def list_schedules(user: CurrentUser = Depends(get_current_user)) -> list[dict]:
    return await scheduler.listing()


@router.post("/v1/schedules", include_in_schema=False)
async def add_schedule(
    body: ScheduleIn,
    user: CurrentUser = Depends(get_current_user),
    settings=Depends(get_settings),
) -> dict:
    if not body.objective.strip():
        raise HTTPException(status_code=400, detail="Say what should be done.")
    if not 0 <= body.hour <= 23 or not 0 <= body.minute <= 59:
        raise HTTPException(status_code=400, detail="That is not a time of day.")
    if any(d < 1 or d > 7 for d in body.days):
        raise HTTPException(status_code=400, detail="Days are 1 (Monday) to 7 (Sunday).")
    return await scheduler.create(
        body.objective, body.hour, body.minute, body.days,
        tz=settings.timezone, max_per_day=max(1, min(body.max_per_day, 24)),
        created_by=f"user:{user.email or user.uid}",
    )


@router.post("/v1/schedules/{schedule_id}/enabled", include_in_schema=False)
async def toggle_schedule(
    schedule_id: UUID, enabled: bool,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    if not await scheduler.set_enabled(schedule_id, enabled):
        raise HTTPException(status_code=404, detail="No such schedule.")
    return {"ok": True, "enabled": enabled}


@router.delete("/v1/schedules/{schedule_id}", include_in_schema=False)
async def remove_schedule(
    schedule_id: UUID, user: CurrentUser = Depends(get_current_user)
) -> dict:
    if not await scheduler.delete(schedule_id):
        raise HTTPException(status_code=404, detail="No such schedule.")
    return {"ok": True}


@router.post("/v1/cron/tick", include_in_schema=False)
async def cron_tick(
    request: Request,
    x_cron_key: str | None = Header(default=None),
    settings=Depends(get_settings),
) -> dict:
    """Let something outside wake a sleeping server on time.

    On a free tier the process sleeps after a quarter of an hour, and a
    loop that is not running cannot notice that anything is due. A free
    pinger calling this every few minutes is what makes a 7am job happen
    at 7am.

    Not behind the owner's session, because a pinger has no session -- so
    it is behind a shared key instead, and with no key configured the
    endpoint does not exist at all. An open trigger for work that spends
    money is not something to leave on by accident.
    """
    if not settings.cron_key:
        raise HTTPException(status_code=404, detail="Not found.")
    supplied = x_cron_key or request.query_params.get("key")
    if not supplied or not secrets.compare_digest(supplied, settings.cron_key):
        raise HTTPException(status_code=403, detail="Bad cron key.")

    await system_control.refuse_if_stopped()
    ran = await scheduler.run_due(settings)
    return {"ran": len(ran), "work": ran}


# --- approving what is waiting ---------------------------------------------

@router.get("/v1/approvals", include_in_schema=False)
async def approvals_waiting(
    user: CurrentUser = Depends(get_current_user)
) -> list[dict]:
    """Everything sitting on your desk, and what each one is waiting for."""
    return await approvals.waiting()


@router.post("/v1/approvals/{task_id}/approve", include_in_schema=False)
async def approve(
    task_id: UUID, body: DecisionIn,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Say yes, and let the work continue.

    The task goes back in the queue rather than being run from here, so
    it passes through the same checks it stopped at. Approving one task is
    not approving a category: the decision is recorded against this task
    and no other.
    """
    await system_control.refuse_if_stopped()

    row = await tasks.get(task_id)
    if row is None or row["status"] != "waiting_approval":
        raise HTTPException(
            status_code=404,
            detail="Nothing is waiting for approval on that task.",
        )

    category = _category_of(row)
    decision = await approvals.decide(
        task_id, category, "approved", f"user:{user.email or user.uid}",
        reason=body.reason,
        # What the owner was looking at. An approval history is only
        # evidence if it records the thing that was approved.
        saw={"objective": row["objective"], "capability": row["capability"],
             "asked": row["failure_reason"], "result": row["result"]},
    )
    if decision is None:
        raise HTTPException(
            status_code=409, detail="That has already been decided."
        )

    await approvals.resume(task_id)
    await telemetry.record(
        "approval_granted", workflow_id=row["workflow_id"], task_id=task_id,
        capability=row["capability"],
        detail={"category": category, "by": f"user:{user.email or user.uid}"},
    )
    if row["workflow_id"]:
        _run_detached(row["workflow_id"])
    return {"ok": True, "resumed": True, "category": category}


@router.post("/v1/approvals/{task_id}/reject", include_in_schema=False)
async def reject(
    task_id: UUID, body: DecisionIn,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Say no. The task is cancelled, not failed.

    Nothing went wrong -- you decided against it -- and the metrics read
    very differently for the two. A rejection rate says something about
    the work; a failure rate says something about the system.
    """
    row = await tasks.get(task_id)
    if row is None or row["status"] != "waiting_approval":
        raise HTTPException(
            status_code=404,
            detail="Nothing is waiting for approval on that task.",
        )

    category = _category_of(row)
    if await approvals.decide(
        task_id, category, "rejected", f"user:{user.email or user.uid}",
        reason=body.reason,
        saw={"objective": row["objective"], "capability": row["capability"],
             "asked": row["failure_reason"], "result": row["result"]},
    ) is None:
        raise HTTPException(status_code=409, detail="That has already been decided.")

    await approvals.abandon(task_id, body.reason or "You decided against it.")
    await telemetry.record(
        "approval_refused", workflow_id=row["workflow_id"], task_id=task_id,
        capability=row["capability"],
        detail={"category": category, "reason": body.reason},
    )
    if row["workflow_id"]:
        await orchestrator.advance(row["workflow_id"], max_waves=1)
    return {"ok": True, "cancelled": True}


def _category_of(task: dict) -> str:
    """Which approval category this task stopped on.

    Read back from the permissions the task declared, so it matches
    whatever the runtime actually stopped at. Defaulting to 'publishing'
    would quietly file a spending decision under the wrong heading, and
    the approval history is meant to be evidence.
    """
    from app.agents.schemas import ALWAYS_APPROVED, Permission

    for name in (task.get("constraints") or {}).get("permissions", []):
        try:
            category = ALWAYS_APPROVED.get(Permission(name))
        except ValueError:
            continue
        if category:
            return category
    return "publishing"
