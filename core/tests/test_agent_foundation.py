"""The Agent Foundation, exercised as the brief specifies (tests A-K).

These run against a real Postgres and the real modules. The only thing
faked is the agent bodies -- what is being proved is the foundation, and
a fake agent that returns a known result is the clearest way to show the
machinery around it behaved.
"""
from decimal import Decimal
from uuid import UUID

import pytest
import pytest_asyncio

from app.agents import (
    context as ctx,
    cost,
    model_router,
    orchestrator,
    permissions,
    registry,
    runtime,
    tasks,
    telemetry,
)
from app.agents.schemas import (
    AgentError,
    AgentResult,
    AgentSpec,
    Lifecycle,
    ModelTier,
    Permission,
    PermissionDenied,
)
from app.config import Settings


@pytest_asyncio.fixture
async def clean(db_pool):
    await db_pool.execute(
        "DELETE FROM agent_metrics; DELETE FROM agent_events; "
        "DELETE FROM tasks; DELETE FROM workflows; DELETE FROM agents; "
        "DELETE FROM memories; DELETE FROM audit_log;"
    )
    registry._IMPLEMENTATIONS.clear()
    yield db_pool
    registry._IMPLEMENTATIONS.clear()


def spec(capability, **kw) -> AgentSpec:
    return AgentSpec(
        capability=capability,
        name=kw.pop("name", capability),
        task_types=kw.pop("task_types", ("general",)),
        permissions=kw.pop("permissions", frozenset({Permission.READ_MEMORY})),
        model_tiers=kw.pop("model_tiers", (ModelTier.STANDARD,)),
        status=kw.pop("status", Lifecycle.ACTIVE),
        **kw,
    )


def returns(output, **kw):
    async def fn(handoff, choice):
        return AgentResult(output=output, tokens_in=100, tokens_out=50, **kw)
    return fn


async def install(capability, fn=None, **kw):
    s = spec(capability, **kw)
    await registry.register(s)
    registry.implement(capability, fn or returns(f"{capability} did the work"))
    return s


# --- Test A: agent registration -------------------------------------------

@pytest.mark.asyncio
async def test_A_register_and_query_the_registry(clean):
    await install("general.research", domain="general")
    await install("general.writer", domain="general", task_types=("writing",))
    await install("secret.thing", status=Lifecycle.EXPERIMENTAL)

    everything = await registry.find(routable_only=False)
    assert len(everything) == 3

    routable = await registry.find()
    assert {a.capability for a in routable} == {"general.research", "general.writer"}, (
        "an experimental agent must never be routable"
    )

    by_type = await registry.find(task_type="writing")
    assert [a.capability for a in by_type] == ["general.writer"]

    one = await registry.resolve("general.research")
    assert one and one.version == 1


# --- Test B: simple delegation --------------------------------------------

@pytest.mark.asyncio
async def test_B_jarvis_creates_a_task_and_routes_it(clean):
    await install("general.research")
    result = await orchestrator.run("Find out what changed in AI today", "user:owner")

    assert result["status"] == "completed"
    assert result["tasks"] == {"completed": 1}
    assert "general.research" in result["outputs"]


# --- Test C: multi-step workflow ------------------------------------------

@pytest.mark.asyncio
async def test_C_dependent_subtasks_run_in_order(clean):
    order = []

    def recorder(name):
        async def fn(handoff, choice):
            order.append(name)
            return AgentResult(output=f"{name} output", tokens_in=10, tokens_out=5)
        return fn

    await install("general.research", recorder("research"))
    await install("general.analysis", recorder("analysis"))
    await install("general.writer", recorder("writer"))

    result = await orchestrator.run(
        "Research, analyse, then write it up", "user:owner",
        steps=[
            orchestrator.Step("general.research", "Research it", name="r"),
            orchestrator.Step("general.analysis", "Analyse it", name="a", after=("r",)),
            orchestrator.Step("general.writer", "Write it", name="w", after=("a",)),
        ],
    )

    assert result["status"] == "completed"
    assert order == ["research", "analysis", "writer"], "dependencies were not honoured"
    assert result["waves"] == 3, "a chain of three cannot finish in fewer waves"


@pytest.mark.asyncio
async def test_C2_independent_tasks_run_together(clean):
    """Two tasks that need nothing from each other must not queue up.

    Concurrency is not a nicety here: a workflow that fans out to six
    lookups should take as long as the slowest, not the sum.
    """
    await install("general.research")
    await install("general.analysis")

    result = await orchestrator.run(
        "Two unrelated things", "user:owner",
        steps=[
            orchestrator.Step("general.research", "One", name="a"),
            orchestrator.Step("general.analysis", "Two", name="b"),
        ],
    )
    assert result["status"] == "completed"
    assert result["waves"] == 1, "independent tasks should run in a single wave"


# --- Test D: context isolation --------------------------------------------

@pytest.mark.asyncio
async def test_D_an_agent_gets_its_task_not_the_global_history(clean):
    """The point of context scoping, demonstrated.

    The writer is registered for owner memory; the analyst is not. The
    analyst must not receive the owner's private facts merely because
    they exist in the database.
    """
    from app.memory import store_memory

    await store_memory(content="Owner's wife is called Sneha", category="people",
                       origin="inferred", confidence=0.8)

    seen = {}

    def capture(name):
        async def fn(handoff, choice):
            seen[name] = handoff.context
            return AgentResult(output="ok", tokens_in=5, tokens_out=5)
        return fn

    await install("general.analysis", capture("analysis"), config={"scopes": ["working"]})
    await install("general.writer", capture("writer"),
                  config={"scopes": ["working", "owner"]})

    await orchestrator.run(
        "Two agents, different needs", "user:owner",
        steps=[
            orchestrator.Step("general.analysis", "Analyse", name="a",
                              inputs={"topic": "quarterly numbers"}),
            orchestrator.Step("general.writer", "Write", name="w",
                              inputs={"topic": "a birthday note"}),
        ],
    )

    assert "Sneha" not in seen["analysis"], "owner memory leaked to an agent without the scope"
    assert "quarterly numbers" in seen["analysis"], "the agent lost its own inputs"
    assert "Sneha" in seen["writer"], "an agent WITH owner scope should receive it"


@pytest.mark.asyncio
async def test_D2_a_task_receives_what_its_dependency_produced(clean):
    seen = {}

    async def producer(handoff, choice):
        return AgentResult(output="the researched answer is 42", tokens_in=5, tokens_out=5)

    async def consumer(handoff, choice):
        seen["context"] = handoff.context
        return AgentResult(output="done", tokens_in=5, tokens_out=5)

    await install("general.research", producer)
    await install("general.analysis", consumer)

    await orchestrator.run(
        "Chain", "user:owner",
        steps=[
            orchestrator.Step("general.research", "Find it", name="r"),
            orchestrator.Step("general.analysis", "Use it", name="a", after=("r",)),
        ],
    )
    assert "42" in seen["context"], "a task must see what it was waiting for"


# --- Test E: permission enforcement ---------------------------------------

@pytest.mark.asyncio
async def test_E_an_agent_cannot_do_what_it_was_not_granted(clean):
    writer = spec("general.writer", permissions=frozenset({Permission.READ_MEMORY}))
    with pytest.raises(PermissionDenied, match="does not hold"):
        permissions.check(writer, Permission.PUBLISH)


@pytest.mark.asyncio
async def test_E2_no_agent_may_ever_rewrite_jarvis(clean):
    """Even holding the permission is not enough.

    An agent that concludes changing JARVIS would be useful must not be
    able to act on that conclusion, however sound its reasoning.
    """
    over_privileged = spec(
        "rogue.agent",
        permissions=frozenset({Permission.MODIFY_CONFIG, Permission.MODIFY_AGENTS}),
    )
    for perm in (Permission.MODIFY_CONFIG, Permission.MODIFY_AGENTS):
        with pytest.raises(PermissionDenied, match="never delegated"):
            permissions.check(over_privileged, perm)


@pytest.mark.asyncio
async def test_E3_a_refused_task_fails_and_is_never_retried(clean):
    """Retrying a permission failure only wastes budget."""
    await install("general.writer", permissions=frozenset({Permission.READ_MEMORY}))
    wf = await tasks.create_workflow("Publish something", "user:owner")
    task_id = await tasks.create(
        objective="Publish it", capability="general.writer", workflow_id=wf,
        constraints={"permissions": ["publish"]},
    )

    assert await runtime.run_task(task_id) is False
    row = await tasks.get(task_id)
    assert row["status"] == "failed"
    assert row["attempts"] == 1, "a refusal must not be retried"


@pytest.mark.asyncio
async def test_E4_delegated_permissions_can_only_narrow(clean):
    supervisor = spec("sup", permissions=frozenset({Permission.READ_MEMORY,
                                                    Permission.NETWORK}))
    passed = permissions.escalate(
        supervisor, frozenset({Permission.NETWORK, Permission.PUBLISH})
    )
    assert passed == frozenset({Permission.NETWORK}), (
        "a supervisor handed on a permission it does not hold"
    )


# --- Test F: model routing ------------------------------------------------

@pytest.mark.asyncio
async def test_F_two_tasks_route_to_different_models(clean):
    settings = Settings(llm_provider="gemini", model_cheap="flash-lite",
                        gemini_model="flash", model_deep="pro")

    cheap = model_router.choose(settings, tier=ModelTier.CHEAP)
    hard = model_router.choose(settings, tier=ModelTier.STANDARD, risk="high")

    assert cheap.model == "flash-lite"
    assert hard.model == "pro"
    assert cheap.model != hard.model
    # The reason matters as much as the choice: "why did this cost so
    # much" needs an answer better than "the router decided".
    assert "expensive" in hard.reason


@pytest.mark.asyncio
async def test_F2_an_agent_cannot_reach_for_a_tier_it_is_not_registered_for(clean):
    settings = Settings(llm_provider="gemini", model_cheap="flash-lite",
                        gemini_model="flash", model_deep="pro")
    choice = model_router.choose(
        settings, tier=ModelTier.DEEP, allowed=(ModelTier.CHEAP,)
    )
    assert choice.tier is ModelTier.CHEAP
    assert "not registered for deep" in choice.reason


# --- Test G: cost tracking ------------------------------------------------

@pytest.mark.asyncio
async def test_G_spend_is_attributed_to_task_and_workflow(clean):
    async def costly(handoff, choice):
        return AgentResult(output="done", cost_inr=Decimal("2.50"),
                           tokens_in=1000, tokens_out=500)

    await install("general.research", costly)
    result = await orchestrator.run("Something", "user:owner")
    wf_id = result["workflow_id"]

    wf = await tasks.get_workflow(wf_id)
    rows = await tasks.workflow_tasks(wf_id)

    assert Decimal(rows[0]["spend_inr"]) == Decimal("2.50"), "task was not charged"
    assert Decimal(wf["spend_inr"]) == Decimal("2.50"), "workflow did not roll up"


@pytest.mark.asyncio
async def test_G2_a_workflow_cannot_overspend_its_budget(clean):
    """Checked before spending. Finding out afterwards is an audit trail,
    not a budget."""
    await install("general.research")
    wf = await tasks.create_workflow("Expensive", "user:owner", budget_inr=Decimal("1.00"))
    task_id = await tasks.create(
        objective="Do it", capability="general.research", workflow_id=wf,
        budget_cost=Decimal("5.00"),
    )

    assert await runtime.run_task(task_id) is False
    row = await tasks.get(task_id)
    assert row["status"] == "failed"
    assert "budget" in row["failure_reason"].lower()


# --- Test H: failure recovery ---------------------------------------------

@pytest.mark.asyncio
async def test_H_a_transient_failure_is_retried_then_succeeds(clean):
    calls = {"n": 0}

    async def flaky(handoff, choice):
        calls["n"] += 1
        if calls["n"] < 3:
            raise AgentError("the upstream API wobbled")
        return AgentResult(output="worked on the third go", tokens_in=5, tokens_out=5)

    await install("general.research", flaky)
    wf = await tasks.create_workflow("Flaky work", "user:owner")
    task_id = await tasks.create(objective="Do it", capability="general.research",
                                 workflow_id=wf, max_attempts=3)

    for _ in range(3):
        await runtime.run_task(task_id)

    row = await tasks.get(task_id)
    assert row["status"] == "completed"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_H2_retries_are_bounded(clean):
    """An endless retry loop is how one broken agent spends a budget."""
    calls = {"n": 0}

    async def always_broken(handoff, choice):
        calls["n"] += 1
        raise AgentError("still broken")

    await install("general.research", always_broken)
    wf = await tasks.create_workflow("Doomed", "user:owner")
    task_id = await tasks.create(objective="Do it", capability="general.research",
                                 workflow_id=wf, max_attempts=2)

    for _ in range(6):
        await runtime.run_task(task_id)

    row = await tasks.get(task_id)
    assert row["status"] == "failed"
    assert calls["n"] == 2, f"attempted {calls['n']} times against a limit of 2"


@pytest.mark.asyncio
async def test_H3_a_missing_capability_fails_cleanly(clean):
    wf = await tasks.create_workflow("Impossible", "user:owner")
    task_id = await tasks.create(objective="Do it", capability="nobody.provides.this",
                                 workflow_id=wf)

    assert await runtime.run_task(task_id) is False
    row = await tasks.get(task_id)
    assert row["status"] == "failed"
    assert "No routable agent" in row["failure_reason"]


# --- Test I: audit trail --------------------------------------------------

@pytest.mark.asyncio
async def test_I_a_finished_workflow_can_be_traced_end_to_end(clean):
    await install("general.research")
    await install("general.analysis")
    result = await orchestrator.run(
        "Trace me", "user:owner",
        steps=[
            orchestrator.Step("general.research", "Find", name="r"),
            orchestrator.Step("general.analysis", "Use", name="a", after=("r",)),
        ],
    )

    trace = await telemetry.trace(result["workflow_id"])
    kinds = [e["kind"] for e in trace]

    for expected in ("workflow_planned", "agent_selected", "context_built",
                     "model_routed", "task_completed", "workflow_settled"):
        assert expected in kinds, f"the trace cannot answer for '{expected}'"

    # Why was this agent selected? The trace must actually say.
    chosen = next(e for e in trace if e["kind"] == "agent_selected")
    assert "why" in chosen["detail"]
    routed = next(e for e in trace if e["kind"] == "model_routed")
    assert routed["detail"]["why"], "the model choice must carry its reason"


# --- Test J: lifecycle and versioning -------------------------------------

@pytest.mark.asyncio
async def test_J_versions_progress_and_only_one_is_live(clean):
    await registry.register(spec("general.research", version=1, status=Lifecycle.ACTIVE))
    await registry.register(spec("general.research", version=2,
                                 status=Lifecycle.EXPERIMENTAL))

    live = await registry.resolve("general.research")
    assert live.version == 1, "an experimental version must not take work"

    await registry.set_status("general.research", 2, Lifecycle.ACTIVE)
    live = await registry.resolve("general.research")
    assert live.version == 2

    all_versions = await registry.versions("general.research")
    assert [(v.version, v.status.value) for v in all_versions] == [
        (2, "active"), (1, "disabled"),
    ], "activating v2 should have stood v1 down, and kept it for rollback"

    # Roll back.
    await registry.set_status("general.research", 1, Lifecycle.ACTIVE)
    assert (await registry.resolve("general.research")).version == 1


@pytest.mark.asyncio
async def test_J2_a_degraded_agent_still_works_but_loses_to_a_healthy_one(clean):
    await registry.register(spec("general.research", version=1, status=Lifecycle.DEGRADED))
    assert (await registry.resolve("general.research")).version == 1, (
        "a degraded agent is better than no agent"
    )
    await registry.register(spec("general.research", version=2, status=Lifecycle.ACTIVE))
    assert (await registry.resolve("general.research")).version == 2


# --- Test K: Scientist data ----------------------------------------------

@pytest.mark.asyncio
async def test_K_performance_cost_and_failure_telemetry_is_queryable(clean):
    async def ok(handoff, choice):
        return AgentResult(output="fine", confidence=0.9, cost_inr=Decimal("0.10"),
                           tokens_in=10, tokens_out=10)

    async def broken(handoff, choice):
        raise AgentError("nope")

    await install("general.research", ok)
    await install("general.analysis", broken, max_cost_inr=None)

    await orchestrator.run(
        "Mixed outcomes", "user:owner",
        steps=[
            orchestrator.Step("general.research", "Works", name="a"),
            orchestrator.Step("general.analysis", "Fails", name="b"),
        ],
    )

    rows = await clean.fetch(
        "SELECT capability, metric, value FROM agent_metrics ORDER BY capability, metric"
    )
    got = {(r["capability"], r["metric"]): float(r["value"]) for r in rows}

    # The questions Scientist will ask: what succeeds, what costs, what fails.
    assert got[("general.research", "success")] == 1
    assert got[("general.research", "confidence")] == pytest.approx(0.9)
    assert got[("general.research", "cost_inr")] == pytest.approx(0.10)
    assert got[("general.analysis", "failure")] == 1

    failures = await clean.fetch(
        "SELECT detail FROM agent_events WHERE kind = 'task_failed'"
    )
    assert failures, "a failure must leave something for Scientist to analyse"


@pytest.mark.asyncio
async def test_K2_expensive_workflows_can_be_found_after_the_fact(clean):
    """One of Scientist's stated jobs, so the data must support it."""
    async def pricey(handoff, choice):
        return AgentResult(output="x", cost_inr=Decimal("3.00"), tokens_in=1, tokens_out=1)

    await install("general.research", pricey)
    await orchestrator.run("Costly", "user:owner")

    rows = await clean.fetch(
        "SELECT objective, spend_inr FROM workflows WHERE spend_inr > 1 "
        "ORDER BY spend_inr DESC"
    )
    assert rows and rows[0]["objective"] == "Costly"


# --- Test L: a gate can stop a graph part-way through ----------------------

@pytest.mark.asyncio
async def test_L_a_gate_stops_the_rest_of_the_plan(clean):
    """Some steps invalidate the ones after them.

    A verification step that finds a claim false makes the writing that
    depends on it worthless, and running it anyway costs real money to
    produce something that has to be thrown away. The gate is asked
    between waves; it decides, it never runs anything.
    """
    ran = []

    def recorder(name):
        async def fn(handoff, choice):
            ran.append(name)
            return AgentResult(output=f"{name} done", tokens_in=1, tokens_out=1)
        return fn

    await install("general.research", recorder("research"))
    await install("general.analysis", recorder("analysis"))
    await install("general.writer", recorder("writer"))

    async def stop_after_the_first(workflow_id):
        return "the first step made the rest pointless" if ran else None

    result = await orchestrator.run(
        "Three steps, stopped after one", "user:owner",
        steps=[
            orchestrator.Step("general.research", "One", name="a"),
            orchestrator.Step("general.analysis", "Two", name="b", after=("a",)),
            orchestrator.Step("general.writer", "Three", name="c", after=("b",)),
        ],
        gate=stop_after_the_first,
    )

    assert ran == ["research"], "the gate did not stop anything"
    rows = await tasks.workflow_tasks(UUID(result["workflow_id"]))
    statuses = {r["capability"]: r["status"] for r in rows}
    assert statuses["general.analysis"] == "cancelled"
    assert statuses["general.writer"] == "cancelled"


@pytest.mark.asyncio
async def test_L2_a_gate_that_never_objects_changes_nothing(clean):
    """The default path must behave exactly as it did without a gate."""
    await install("general.research")
    await install("general.analysis")

    async def never(workflow_id):
        return None

    result = await orchestrator.run(
        "Two steps", "user:owner",
        steps=[
            orchestrator.Step("general.research", "One", name="a"),
            orchestrator.Step("general.analysis", "Two", name="b", after=("a",)),
        ],
        gate=never,
    )
    assert result["status"] == "completed"
    assert result["tasks"] == {"completed": 2}
