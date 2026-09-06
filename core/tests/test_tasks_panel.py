"""The door into the agent foundation.

Four days of capabilities were reachable only by posting JSON, which from
an iPad is not reachable at all. These cover the route side of the Tasks
panel: that planning creates nothing, that running returns immediately
rather than holding the request open for half a minute, that what runs is
what was approved, and that none of it is available to someone who is not
the owner.
"""
import asyncio
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from app.agents import builtin, planner, registry, tasks
from app.auth import get_current_user
from app.routes import agents as agents_route

OWNER = SimpleNamespace(uid="owner-uid", email="sagarnandani99@gmail.com")


@pytest_asyncio.fixture
async def clean(db_pool):
    await db_pool.execute(
        "DELETE FROM agent_metrics; DELETE FROM agent_events; "
        "DELETE FROM tasks; DELETE FROM workflows; DELETE FROM agents;"
    )
    registry._IMPLEMENTATIONS.clear()
    await builtin.install()
    yield db_pool
    registry._IMPLEMENTATIONS.clear()


def _asgi(app) -> httpx.AsyncClient:
    """Drive the app in the test's own event loop.

    Not TestClient: that runs the app on a loop of its own, and the
    database pool belongs to this one. Sharing an asyncpg connection
    across two loops fails with "another operation is in progress" --
    which reads like a bug in the route and is a bug in the harness.
    """
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


@pytest_asyncio.fixture
async def client(clean):
    """The real router, with the door already opened.

    Authentication has its own tests; overriding it here keeps these
    about the routes rather than about signing in again.
    """
    app = FastAPI()
    app.include_router(agents_route.router)
    app.dependency_overrides[get_current_user] = lambda: OWNER
    async with _asgi(app) as c:
        yield c


def stub_plan(monkeypatch, steps, reasoning="because", rejected=()):
    async def propose(objective, *, max_steps=None):
        return planner.Plan(steps=list(steps), reasoning=reasoning,
                            rejected=list(rejected), source="model",
                            model="planner-test")

    monkeypatch.setattr(planner, "propose", propose)


def step(capability, objective, name="", after=()):
    from app.agents.schemas import Step

    return Step(capability=capability, objective=objective, name=name,
                after=tuple(after))


# --- planning spends nothing ----------------------------------------------

@pytest.mark.asyncio
async def test_asking_for_a_plan_creates_no_work(client, monkeypatch):
    """The whole point of the two-step: read before anything runs."""
    stub_plan(monkeypatch, [step("general.research", "Find it", "r")],
              reasoning="One step is enough.", rejected=["ignored something"])

    body = (await client.post("/v1/plans", json={"objective": "Find something out"})).json()

    assert body["reasoning"] == "One step is enough."
    assert body["steps"][0]["capability"] == "general.research"
    assert body["rejected"] == ["ignored something"]

    listed = (await client.get("/v1/workflows")).json()
    assert listed == [], "planning created work the owner had not approved"


# --- running --------------------------------------------------------------

@pytest.mark.asyncio
async def test_starting_a_workflow_answers_before_the_work_is_done(client, monkeypatch):
    """A research-then-check run takes half a minute or more.

    Holding the request open for it means a spinner on a phone, a proxy
    timeout, and no way to close the tab and come back. So the response
    carries the task rows and the work continues behind it.
    """
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(workflow_id):
        started.set()
        await release.wait()

    monkeypatch.setattr(agents_route.orchestrator, "advance", slow)

    body = (await client.post("/v1/workflows", json={
        "objective": "Do a thing",
        "steps": [{"capability": "general.research", "objective": "Find it"}],
    })).json()

    assert body["status"] == "running"
    assert len(body["tasks"]) == 1
    assert body["tasks"][0]["capability"] == "general.research"
    release.set()


@pytest.mark.asyncio
async def test_what_runs_is_what_was_approved(client, monkeypatch):
    """The approved steps go back verbatim; the planner must not re-run.

    Re-planning at run time would execute something nobody read, which
    would make the approval step decorative.
    """
    def refuse(*a, **k):
        raise AssertionError("the planner ran on an approved plan")

    monkeypatch.setattr(planner, "propose", refuse)
    monkeypatch.setattr(agents_route.orchestrator, "advance",
                        lambda workflow_id: asyncio.sleep(0))

    body = (await client.post("/v1/workflows", json={
        "objective": "Do it my way",
        "steps": [
            {"capability": "general.research", "objective": "First", "name": "a"},
            {"capability": "general.writer", "objective": "Second",
             "name": "b", "after": ["a"]},
        ],
    })).json()

    caps = [t["capability"] for t in body["tasks"]]
    assert caps == ["general.research", "general.writer"]


@pytest.mark.asyncio
async def test_an_objective_nothing_can_take_does_not_start_a_runner(client, monkeypatch):
    """Planning can settle a workflow before any task exists.

    Starting a runner for that would only rediscover it, and reporting
    "running" would leave a spinner turning over nothing.
    """
    stub_plan(monkeypatch, [], reasoning="Nothing here can send email.")
    ran = False

    async def advance(workflow_id):
        nonlocal ran
        ran = True

    monkeypatch.setattr(agents_route.orchestrator, "advance", advance)

    body = (await client.post("/v1/workflows", json={"objective": "Email my accountant"})).json()

    assert body["status"] == "failed"
    assert body["tasks"] == []
    assert "send email" in body["failure_reason"]
    assert ran is False


# --- watching it ----------------------------------------------------------

@pytest.mark.asyncio
async def test_progress_can_be_read_back_while_it_runs(client, monkeypatch):
    monkeypatch.setattr(agents_route.orchestrator, "advance",
                        lambda workflow_id: asyncio.sleep(0))

    started = (await client.post("/v1/workflows", json={
        "objective": "Watch me",
        "steps": [{"capability": "general.research", "objective": "Find it"}],
    })).json()

    body = (await client.get(f"/v1/workflows/{started['workflow_id']}")).json()
    assert body["workflow"]["objective"] == "Watch me"
    assert len(body["tasks"]) == 1
    assert body["trace"], "the audit trail is empty"


@pytest.mark.asyncio
async def test_recent_work_is_listed_newest_first(client, monkeypatch):
    monkeypatch.setattr(agents_route.orchestrator, "advance",
                        lambda workflow_id: asyncio.sleep(0))

    for name in ("First job", "Second job"):
        await client.post("/v1/workflows", json={
            "objective": name,
            "steps": [{"capability": "general.research", "objective": "x"}],
        })

    rows = (await client.get("/v1/workflows?limit=10")).json()
    assert [r["objective"] for r in rows] == ["Second job", "First job"]
    assert rows[0]["task_count"] == 1


@pytest.mark.asyncio
async def test_the_listing_is_bounded(client, monkeypatch):
    """A limit the caller sets is a limit the caller can abuse."""
    monkeypatch.setattr(agents_route.orchestrator, "advance",
                        lambda workflow_id: asyncio.sleep(0))
    assert (await client.get("/v1/workflows?limit=9999")).status_code == 200
    assert (await client.get("/v1/workflows?limit=0")).status_code == 200


# --- who may use it -------------------------------------------------------

@pytest.mark.parametrize("method,path", [
    ("post", "/v1/plans"), ("post", "/v1/workflows"),
    ("get", "/v1/workflows"), ("get", "/v1/agents"),
])
@pytest.mark.asyncio
async def test_none_of_this_is_open_to_a_stranger(clean, method, path):
    """Spending money is not something a passer-by gets to start."""
    app = FastAPI()
    app.include_router(agents_route.router)
    async with _asgi(app) as c:
        kwargs = {"json": {"objective": "x"}} if method == "post" else {}
        res = await getattr(c, method)(path, **kwargs)
    assert res.status_code in (401, 403), f"{path} answered {res.status_code}"
