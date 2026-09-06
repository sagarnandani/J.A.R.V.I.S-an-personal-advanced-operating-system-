"""A thin window onto the Agent Foundation.

Deliberately small. The brief says not to turn JARVIS into an engineering
dashboard, so this exposes what is needed to run and inspect work and
nothing more. The owner still talks to JARVIS; this is for the machinery
behind it.
"""
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.agents import orchestrator, registry, tasks, telemetry
from app.auth import CurrentUser, get_current_user

router = APIRouter()


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
    steps = [
        orchestrator.Step(
            capability=s.capability, objective=s.objective, name=s.name,
            after=tuple(s.after), inputs=s.inputs,
            expected_output=s.expected_output, constraints=s.constraints,
        )
        for s in body.steps
    ] or None
    return await orchestrator.run(
        body.objective, f"user:{user.email or user.uid}", steps, body.budget_inr
    )


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
