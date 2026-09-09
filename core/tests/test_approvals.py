"""Saying yes, and what happens next.

The foundation could always stop and ask. Until now it could not be
answered: a task that needed approval moved to 'waiting_approval' and
stayed there for ever. Every publishing gate in the Media Company rests
on this working.
"""
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio

from app.agents import approvals, registry, runtime, tasks
from app.agents.schemas import (
    AgentResult,
    AgentSpec,
    Lifecycle,
    ModelTier,
    Permission,
)


@pytest_asyncio.fixture
async def clean(db_pool):
    await db_pool.execute(
        "DELETE FROM task_approvals; DELETE FROM agent_metrics; "
        "DELETE FROM agent_events; DELETE FROM tasks; DELETE FROM workflows; "
        "DELETE FROM agents; DELETE FROM system_control WHERE key='emergency_stop';"
    )
    registry._IMPLEMENTATIONS.clear()
    yield db_pool
    registry._IMPLEMENTATIONS.clear()


PUBLISHER = AgentSpec(
    capability="test.publisher", name="Publisher",
    task_types=("general",),
    permissions=frozenset({Permission.PUBLISH, Permission.READ_MEMORY}),
    model_tiers=(ModelTier.CHEAP,), status=Lifecycle.ACTIVE,
    config={"scopes": ["working"]},
)


async def _a_task_awaiting_approval(runs: list) -> tuple:
    """A registered publisher, stopped at the gate. The starting point."""
    await registry.register(PUBLISHER)

    async def run(handoff, choice):
        runs.append(handoff.task_id)
        return AgentResult(output="posted", confidence=1.0)

    registry.implement("test.publisher", run)

    wf = await tasks.create_workflow("publish something", "user:owner")
    task_id = await tasks.create(
        objective="post the video", capability="test.publisher", workflow_id=wf,
        constraints={"permissions": ["publish"]},
    )
    assert await runtime.run_task(task_id) is False
    row = await tasks.get(task_id)
    assert row["status"] == "waiting_approval", "it did not stop at the gate"
    return wf, task_id


# --- the gate holds -------------------------------------------------------

@pytest.mark.asyncio
async def test_publishing_stops_and_waits(clean):
    runs = []
    _, task_id = await _a_task_awaiting_approval(runs)
    assert not runs, "it published before anyone said yes"


@pytest.mark.asyncio
async def test_it_is_listed_as_waiting_with_what_it_wants(clean):
    """The owner has to be able to see what is on their desk."""
    _, task_id = await _a_task_awaiting_approval([])
    waiting = await approvals.waiting()

    assert len(waiting) == 1
    assert waiting[0]["id"] == task_id
    assert "publish" in waiting[0]["asking_for"].lower()
    assert waiting[0]["objective"] == "post the video"


# --- and can now be opened ------------------------------------------------

@pytest.mark.asyncio
async def test_approving_lets_the_work_finish(clean):
    """The whole point. Before this, the task waited for ever."""
    runs = []
    _, task_id = await _a_task_awaiting_approval(runs)

    await approvals.decide(task_id, "publishing", "approved", "user:owner")
    assert await approvals.resume(task_id) is True
    assert await runtime.run_task(task_id) is True

    assert runs == [task_id], "approved work still did not run"
    assert (await tasks.get(task_id))["status"] == "completed"


@pytest.mark.asyncio
async def test_a_resumed_task_goes_back_through_the_same_checks(clean):
    """Not around them.

    A resume path that skipped the runtime would mean the one action the
    owner explicitly approved is the one action nobody checked. So the
    task is re-queued and run normally -- it reaches the same permission
    gate and passes because the decision is now on record.
    """
    runs = []
    _, task_id = await _a_task_awaiting_approval(runs)
    await approvals.decide(task_id, "publishing", "approved", "user:owner")

    # Resuming only re-queues. Nothing has run yet.
    assert await approvals.resume(task_id) is True
    assert not runs, "resume ran the task itself, going round the runtime"
    assert (await tasks.get(task_id))["status"] == "queued"

    # The work happens when the runtime picks it up, which is where every
    # permission, budget and cost check lives.
    assert await runtime.run_task(task_id) is True
    assert runs == [task_id]


@pytest.mark.asyncio
async def test_approving_one_task_is_not_approving_publishing(clean):
    """The difference is the entire point of asking.

    A decision is recorded against one task. A second publishing task
    stops at the gate exactly as the first one did.
    """
    runs = []
    _, first = await _a_task_awaiting_approval(runs)
    await approvals.decide(first, "publishing", "approved", "user:owner")
    await approvals.resume(first)
    await runtime.run_task(first)

    second = await tasks.create(
        objective="post another video", capability="test.publisher",
        constraints={"permissions": ["publish"]},
    )
    assert await runtime.run_task(second) is False
    assert (await tasks.get(second))["status"] == "waiting_approval"
    assert runs == [first], "a second post went out on the first one's approval"


# --- saying no ------------------------------------------------------------

@pytest.mark.asyncio
async def test_rejecting_cancels_rather_than_fails(clean):
    """Nothing went wrong; the owner decided against it.

    The metrics read very differently: a rejection rate says something
    about the work, a failure rate says something about the system.
    """
    runs = []
    _, task_id = await _a_task_awaiting_approval(runs)

    await approvals.decide(task_id, "publishing", "rejected", "user:owner",
                           reason="the hook is weak")
    assert await approvals.abandon(task_id, "the hook is weak") is True

    row = await tasks.get(task_id)
    assert row["status"] == "cancelled"
    assert "hook" in row["failure_reason"]
    assert not runs


@pytest.mark.asyncio
async def test_rejecting_closes_what_was_waiting_on_it(clean):
    """Otherwise a downstream task blocks for ever with no explanation."""
    from app.db import execute

    _, task_id = await _a_task_awaiting_approval([])
    after = await tasks.create(objective="tell the owner", capability="test.publisher")
    await execute("UPDATE tasks SET depends_on = $2, status = 'blocked' WHERE id = $1",
                  after, [task_id])

    await approvals.decide(task_id, "publishing", "rejected", "user:owner")
    await approvals.abandon(task_id, "no")

    row = await tasks.get(after)
    assert row["status"] == "cancelled"
    assert "depended on was rejected" in row["failure_reason"]


# --- a decision cannot be quietly reversed --------------------------------

@pytest.mark.asyncio
async def test_a_decision_is_made_once(clean):
    """Approving twice is not twice as approved.

    Without this, a rejection could be overwritten by a later yes and the
    approval history would stop being evidence of anything.
    """
    _, task_id = await _a_task_awaiting_approval([])

    assert await approvals.decide(task_id, "publishing", "rejected", "user:owner")
    assert await approvals.decide(task_id, "publishing", "approved", "user:owner") is None
    assert not await approvals.granted(task_id, "publishing")


@pytest.mark.asyncio
async def test_what_the_owner_saw_is_recorded(clean):
    """An approval history that does not say what was approved is not
    evidence, and staged autonomy is meant to be built on it."""
    _, task_id = await _a_task_awaiting_approval([])
    await approvals.decide(
        task_id, "publishing", "approved", "user:owner",
        saw={"objective": "post the video", "asked": "publish needs approval"},
    )

    row = await clean.fetchrow("SELECT * FROM task_approvals WHERE task_id = $1", task_id)
    assert row["decided_by"] == "user:owner"
    assert row["saw"]["objective"] == "post the video"
    assert row["decided_at"] is not None


# --- through the real routes ---------------------------------------------

@pytest_asyncio.fixture
async def api(clean):
    import httpx
    from fastapi import FastAPI

    from app.auth import get_current_user
    from app.config import Settings, get_settings
    from app.routes import agents as agents_route

    app = FastAPI()
    app.include_router(agents_route.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        uid="owner", email="owner@example.com")
    app.dependency_overrides[get_settings] = lambda: Settings()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_the_owner_can_approve_from_the_api(api, clean):
    runs = []
    _, task_id = await _a_task_awaiting_approval(runs)

    listed = (await api.get("/v1/approvals")).json()
    assert len(listed) == 1 and listed[0]["id"] == str(task_id)

    body = (await api.post(f"/v1/approvals/{task_id}/approve",
                           json={"reason": "looks right"})).json()
    assert body["ok"] and body["category"] == "publishing"
    assert (await tasks.get(task_id))["status"] in ("queued", "running", "completed")


@pytest.mark.asyncio
async def test_deciding_twice_through_the_api_is_refused(api, clean):
    _, task_id = await _a_task_awaiting_approval([])
    assert (await api.post(f"/v1/approvals/{task_id}/reject", json={})).status_code == 200
    second = await api.post(f"/v1/approvals/{task_id}/approve", json={})
    assert second.status_code in (404, 409)


@pytest.mark.asyncio
async def test_approving_something_that_is_not_waiting_is_refused(api, clean):
    from uuid import uuid4

    assert (await api.post(f"/v1/approvals/{uuid4()}/approve", json={})).status_code == 404


@pytest.mark.asyncio
async def test_approval_is_refused_while_jarvis_is_stopped(api, clean):
    """Approving resumes work that spends money."""
    from app import system_control

    _, task_id = await _a_task_awaiting_approval([])
    await system_control.set_stopped(True)
    try:
        res = await api.post(f"/v1/approvals/{task_id}/approve", json={})
        assert res.status_code == 503
    finally:
        await system_control.set_stopped(False)


# --- shadow cost ----------------------------------------------------------

def test_shadow_cost_is_never_zero_where_real_cost_is():
    """The reason it exists: "cost Rs.0, therefore return infinite"."""
    from app.agents import cost
    from app.config import Settings

    s = Settings()
    real = cost.price(100_000, 50_000, "gemini", s)
    shadow = cost.shadow(100_000, 50_000, "gemini", s)

    assert real == 0, "the free tier is genuinely free; that should stay true"
    assert shadow > 0, "there is still no way to compare one workflow with another"


def test_shadow_and_real_cost_are_separate_numbers():
    """Never summed. The budget guard reads the real one, and a guard that
    counts imaginary money would refuse real work."""
    from app.agents import cost
    from app.config import Settings

    s = Settings()
    assert cost.price(1000, 1000, "claude", s) > 0
    # Claude is already paid, so its shadow rate is its real rate.
    assert cost.shadow(1000, 1000, "claude", s) == cost.price(1000, 1000, "claude", s)


@pytest.mark.asyncio
async def test_a_task_records_both(clean):
    from app.agents import cost

    wf = await tasks.create_workflow("x", "user:owner")
    task_id = await tasks.create(objective="x", capability="test.publisher",
                                 workflow_id=wf)
    await cost.charge(task_id, wf, Decimal("0"), Decimal("1.2345"))

    row = await tasks.get(task_id)
    assert Decimal(row["spend_inr"]) == 0
    assert Decimal(row["shadow_inr"]) == Decimal("1.2345")
    # The workflow's spend stays real money only.
    wf_row = await tasks.get_workflow(wf)
    assert Decimal(wf_row["spend_inr"]) == 0
