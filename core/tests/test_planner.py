"""Autonomous planning -- and, mostly, what a bad plan cannot do.

The planner is the one component that can invent work nobody asked for,
so these tests spend far more effort on refusal than on success. What is
being proved is that a plan cannot name code that does not exist, cannot
widen its own permissions or budget, cannot smuggle in an ordering that
looks right and is not, and cannot fail in a way that takes the owner's
request down with it.
"""
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio

from app.agents import builtin, orchestrator, planner, registry, tasks, telemetry
from app.agents.schemas import Permission, Step
from app.config import Settings

SETTINGS = Settings(planner_enabled=True, planner_max_steps=5)


@pytest_asyncio.fixture
async def clean(db_pool):
    await db_pool.execute(
        "DELETE FROM agent_metrics; DELETE FROM agent_events; "
        "DELETE FROM tasks; DELETE FROM workflows; DELETE FROM agents;"
    )
    registry._IMPLEMENTATIONS.clear()
    yield db_pool
    registry._IMPLEMENTATIONS.clear()


def stub_planner_model(monkeypatch, reply, settings=SETTINGS):
    """Stand in for the model that writes the plan, recording its prompt."""
    seen = {}

    class Fake:
        async def complete(self, message, history=None, memory_context=None):
            seen["prompt"] = message
            if isinstance(reply, Exception):
                raise reply
            text = reply if isinstance(reply, str) else json.dumps(reply)
            return SimpleNamespace(text=text, input_tokens=200, output_tokens=60,
                                   model="planner-test", provider="fake")

    import app.llm

    monkeypatch.setattr(app.llm, "get_provider", lambda s: Fake())
    monkeypatch.setattr(planner, "get_settings", lambda: settings)
    return seen


def plan_json(*steps, reasoning="because"):
    return {"reasoning": reasoning, "steps": list(steps)}


def step(name, capability, objective="do it", after=(), **extra):
    return {"name": name, "capability": capability, "objective": objective,
            "after": list(after), **extra}


# --- it plans ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_valid_plan_becomes_steps(clean, monkeypatch):
    await builtin.install()
    seen = stub_planner_model(monkeypatch, plan_json(
        step("research", "general.research", "Find the numbers"),
        step("write", "general.writer", "Write it up", after=["research"]),
        reasoning="Facts first, prose second."))

    plan = await planner.propose("Write me a briefing on Indian EV sales")

    assert plan.source == "model"
    assert [s.capability for s in plan.steps] == ["general.research", "general.writer"]
    assert plan.steps[1].after == ("research",)
    assert plan.reasoning == "Facts first, prose second."
    # The catalogue is the registry's, not the model's imagination.
    registered = {s.capability for s in await registry.find()}
    assert registered
    assert all(c in seen["prompt"] for c in registered)
    assert "system.shell" not in seen["prompt"]


@pytest.mark.asyncio
async def test_the_catalogue_is_built_from_what_is_actually_registered(clean, monkeypatch):
    """A planner can only propose what exists, because that is all it sees."""
    from app.agents.capabilities import factcheck_claims, research_web

    await builtin.install()
    await research_web.install()
    await factcheck_claims.install()
    seen = stub_planner_model(monkeypatch, plan_json(
        step("r", "research.web", "Find out")))

    await planner.propose("What happened?")

    assert "research.web" in seen["prompt"]
    assert "factcheck.claims" in seen["prompt"]
    assert "network" in seen["prompt"], "what an agent may touch was not shown"


# --- what a plan cannot do --------------------------------------------------

@pytest.mark.asyncio
async def test_a_plan_naming_code_that_does_not_exist_is_refused(clean, monkeypatch):
    """No path from 'the model wrote a word' to 'something ran'."""
    await builtin.install()
    stub_planner_model(monkeypatch, plan_json(
        step("hack", "system.shell", "Run a command"),
        step("ok", "general.research", "Also do this")))

    plan = await planner.propose("Do something")

    assert plan.source == "direct", "an unregistered capability survived planning"
    assert "system.shell" not in [s.capability for s in plan.steps]
    assert any("system.shell" in r for r in plan.rejected)


@pytest.mark.asyncio
async def test_a_plan_cannot_write_its_own_constraints_or_budget(clean, monkeypatch):
    """The fields that decide permissions and spend are not the plan's to set.

    The runtime reads a task's constraints when deciding which approvals
    apply. A planner able to write them could plan its way around an
    approval, so it may not write them at all -- and what it tried is
    recorded rather than quietly dropped.
    """
    await builtin.install()
    stub_planner_model(monkeypatch, plan_json(step(
        "w", "general.writer", "Publish the piece",
        constraints={"permissions": ["publish"], "risk": "low"},
        budget_inr="9999", inputs={"secret": "x"})))

    plan = await planner.propose("Publish something")

    assert plan.source == "model"
    assert plan.steps[0].constraints is None
    assert plan.steps[0].budget_inr is None
    assert plan.steps[0].inputs is None
    assert any("constraints" in r and "budget_inr" in r for r in plan.rejected)


@pytest.mark.asyncio
async def test_a_planned_agent_still_only_holds_what_the_registry_gave_it(clean, monkeypatch):
    """Planning chooses who runs. It does not change what they may do."""
    await builtin.install()
    stub_planner_model(monkeypatch, plan_json(step(
        "w", "general.writer", "Write and publish it",
        constraints={"permissions": ["publish"]})))

    plan = await planner.propose("Write and publish a post")
    spec = await registry.resolve(plan.steps[0].capability)

    assert Permission.PUBLISH not in spec.permissions


@pytest.mark.asyncio
async def test_a_plan_that_loops_is_refused(clean, monkeypatch):
    """A cycle would not crash -- it would look exactly like a stuck workflow."""
    await builtin.install()
    stub_planner_model(monkeypatch, plan_json(
        step("a", "general.research", "One", after=["b"]),
        step("b", "general.analysis", "Two", after=["a"])))

    plan = await planner.propose("Go round in circles")

    assert plan.source == "direct"
    assert any("loop" in r for r in plan.rejected)


@pytest.mark.asyncio
async def test_a_dependency_on_a_step_that_does_not_exist_refuses_the_whole_plan(
    clean, monkeypatch
):
    """Not just the edge. Dropping it would reorder the work silently.

    A checking step whose dependency was quietly removed starts before
    the thing it checks, finds nothing, and reports success. Refusing the
    plan is the only outcome that cannot be mistaken for working.
    """
    await builtin.install()
    stub_planner_model(monkeypatch, plan_json(
        step("research", "general.research", "Find it"),
        step("check", "general.analysis", "Check it", after=["resarch"])))

    plan = await planner.propose("Find it and check it")

    assert plan.source == "direct"
    assert len(plan.steps) == 1
    assert any("resarch" in r for r in plan.rejected)


@pytest.mark.asyncio
async def test_too_many_steps_is_refused_rather_than_trimmed(clean, monkeypatch):
    """Trimming a graph orphans dependencies and costs money doing it."""
    await builtin.install()
    stub_planner_model(monkeypatch, plan_json(
        *[step(f"s{i}", "general.research", f"Part {i}") for i in range(8)]))

    plan = await planner.propose("A big job")

    assert plan.source == "direct"
    assert any("limit is 5" in r for r in plan.rejected)


@pytest.mark.asyncio
async def test_the_step_cap_can_be_lowered_for_one_plan(clean, monkeypatch):
    await builtin.install()
    stub_planner_model(monkeypatch, plan_json(
        step("a", "general.research", "One"), step("b", "general.analysis", "Two")))

    assert (await planner.propose("x", max_steps=1)).source == "direct"
    assert (await planner.propose("x", max_steps=2)).source == "model"


@pytest.mark.asyncio
async def test_two_steps_with_the_same_name_are_refused(clean, monkeypatch):
    """Ambiguous names make `after` mean two things at once."""
    await builtin.install()
    stub_planner_model(monkeypatch, plan_json(
        step("a", "general.research", "One"), step("a", "general.analysis", "Two")))

    plan = await planner.propose("x")
    assert plan.source == "direct"
    assert any("both called 'a'" in r for r in plan.rejected)


# --- it cannot fail into silence -------------------------------------------

@pytest.mark.parametrize("reply", [
    "I'd suggest starting with some research, then writing it up.",
    "",
    '{"steps": "not a list"}',
    '{"steps": [{"capability": "general.research"}]}',   # no objective
])
@pytest.mark.asyncio
async def test_junk_falls_back_instead_of_breaking_the_request(
    clean, monkeypatch, reply
):
    await builtin.install()
    stub_planner_model(monkeypatch, reply)

    plan = await planner.propose("Do something useful")

    assert plan.source == "direct"
    assert len(plan.steps) == 1
    assert plan.rejected, "a fallback happened with no reason recorded"


@pytest.mark.asyncio
async def test_a_failed_planning_call_falls_back(clean, monkeypatch):
    """Planning must not be able to break the request it was helping with."""
    await builtin.install()
    stub_planner_model(monkeypatch, RuntimeError("503 backend unavailable"))

    plan = await planner.propose("Do something")

    assert plan.source == "direct"
    assert any("503" in r for r in plan.rejected)


@pytest.mark.asyncio
async def test_nothing_fits_is_a_real_answer_not_a_fallback(clean, monkeypatch):
    """Better than picking the nearest capability and sounding confident."""
    await builtin.install()
    stub_planner_model(monkeypatch, plan_json(
        reasoning="Nothing here can send an email."))

    plan = await planner.propose("Email my accountant")

    assert plan.source == "model"
    assert plan.steps == []
    assert "send an email" in plan.reasoning


@pytest.mark.asyncio
async def test_an_empty_registry_plans_nothing(clean, monkeypatch):
    stub_planner_model(monkeypatch, plan_json(step("a", "general.research", "One")))
    plan = await planner.propose("Do something")
    assert plan.source == "none" and plan.steps == []


# --- the owner's off switch -------------------------------------------------

@pytest.mark.asyncio
async def test_planning_can_be_switched_off(clean, monkeypatch):
    """'Decide your own work' is the one capability worth withdrawing.

    Off, JARVIS does what it did before this module existed -- and says so,
    rather than appearing to have planned.
    """
    await builtin.install()
    seen = stub_planner_model(
        monkeypatch, plan_json(step("a", "general.research", "One")),
        settings=Settings(planner_enabled=False, planner_max_steps=5))

    plan = await planner.propose("Do something")

    assert "prompt" not in seen, "the planning model was called despite being off"
    assert plan.source == "direct"
    assert any("switched off" in r for r in plan.rejected)


# --- planning is not doing --------------------------------------------------

@pytest.mark.asyncio
async def test_proposing_creates_nothing(clean, monkeypatch):
    """A plan is a proposal until somebody starts it."""
    await builtin.install()
    stub_planner_model(monkeypatch, plan_json(
        step("a", "general.research", "One"), step("b", "general.writer", "Two")))

    await orchestrator.propose("Do something")

    assert await clean.fetchval("SELECT count(*) FROM workflows") == 0
    assert await clean.fetchval("SELECT count(*) FROM tasks") == 0


@pytest.mark.asyncio
async def test_explicit_steps_always_beat_a_proposal(clean, monkeypatch):
    """Somebody wrote those on purpose. A proposal must not override them."""
    await builtin.install()
    seen = stub_planner_model(monkeypatch, plan_json(
        step("other", "general.writer", "Something else entirely")))

    workflow_id = await orchestrator.start(
        "Do it my way", "user:owner",
        steps=[Step("general.analysis", "Exactly this", name="mine")])

    assert "prompt" not in seen, "the planner ran despite explicit steps"
    rows = await tasks.workflow_tasks(workflow_id)
    assert [r["capability"] for r in rows] == ["general.analysis"]


# --- through the foundation -------------------------------------------------

@pytest.mark.asyncio
async def test_an_unplanned_objective_runs_end_to_end(clean, monkeypatch):
    """The point of the whole module: no steps given, and work happens."""
    await builtin.install()
    stub_planner_model(monkeypatch, plan_json(
        step("research", "general.research", "Find the numbers"),
        step("write", "general.writer", "Write it up", after=["research"]),
        reasoning="Facts first, prose second."))

    result = await orchestrator.run("Brief me on Indian EV sales", "user:owner")

    assert result["status"] == "completed", result
    assert result["tasks"]["completed"] == 2
    assert result["waves"] == 2, "the ordering the plan asked for was not honoured"


@pytest.mark.asyncio
async def test_the_plan_is_in_the_trace_before_anything_runs(clean, monkeypatch):
    """Why these steps is the first question asked of an autonomous plan."""
    await builtin.install()
    stub_planner_model(monkeypatch, plan_json(
        step("a", "general.research", "Find it"),
        step("nope", "system.shell", "Run something"),
        reasoning="Research, then a shell."))

    workflow_id = await orchestrator.start("Do something", "user:owner")
    trace = await telemetry.trace(workflow_id)

    assert trace[0]["kind"] == "plan_proposed", "the plan was not recorded first"
    detail = trace[0]["detail"]
    assert detail["source"] == "direct"
    assert any("system.shell" in r for r in detail["rejected"])


@pytest.mark.asyncio
async def test_an_objective_nothing_can_take_fails_with_the_reason(clean, monkeypatch):
    await builtin.install()
    stub_planner_model(monkeypatch, plan_json(
        reasoning="Nothing registered can send an email."))

    result = await orchestrator.run("Email my accountant", "user:owner")

    assert result["status"] == "failed"
    wf = await tasks.get_workflow(result["workflow_id"])
    assert "send an email" in wf["failure_reason"]
