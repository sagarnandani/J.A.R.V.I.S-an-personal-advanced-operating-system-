"""The organisation chart, and the promise that it is not a drawing.

Every test here changes the registry and then asks what the chart says.
That is the whole point of the page: an agent registered, reassigned,
degraded or retired has to show up without anybody editing a diagram, and
the only way to know that holds is to move something and look.

The other half is about honesty with numbers. A capability that has never
run must not report a zero success rate, because zero and unmeasured look
identical on a dashboard and mean opposite things.
"""
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio

from app.agents import org, registry
from app.agents.schemas import (
    AgentSpec,
    Lifecycle,
    ModelTier,
    Permission,
)
from app.config import Settings

SETTINGS = Settings(gemini_api_key="fake", gemini_model="gemini-test")


@pytest_asyncio.fixture
async def clean(db_pool):
    await db_pool.execute(
        "DELETE FROM agent_metrics; DELETE FROM agent_events; "
        "DELETE FROM tasks; DELETE FROM workflows; DELETE FROM agents;"
    )
    registry._IMPLEMENTATIONS.clear()
    yield db_pool
    registry._IMPLEMENTATIONS.clear()


async def install(capability, **kw):
    spec = AgentSpec(
        capability=capability,
        name=kw.pop("name", capability),
        description=kw.pop("description", "does a thing"),
        domain=kw.pop("domain", None),
        supervisor=kw.pop("supervisor", None),
        task_types=kw.pop("task_types", ("general",)),
        tools=kw.pop("tools", ()),
        permissions=kw.pop("permissions", frozenset({Permission.READ_MEMORY})),
        model_tiers=kw.pop("model_tiers", (ModelTier.STANDARD,)),
        status=kw.pop("status", Lifecycle.ACTIVE),
        **kw,
    )
    await registry.register(spec)
    return spec


def find(node, node_id):
    if node["id"] == node_id:
        return node
    for child in node["children"]:
        found = find(child, node_id)
        if found:
            return found
    return None


def parent_of(root, node_id):
    for child in root["children"]:
        if child["id"] == node_id:
            return root
        found = parent_of(child, node_id)
        if found:
            return found
    return None


# --- the chart is the registry ---------------------------------------------

@pytest.mark.asyncio
async def test_an_empty_registry_is_jarvis_alone(clean):
    """No invented agents, not even placeholders for ones that will exist."""
    chart = await org.tree()
    assert chart["root"]["id"] == "jarvis"
    assert chart["root"]["children"] == []
    assert chart["counts"]["agents"] == 0


@pytest.mark.asyncio
async def test_a_new_agent_appears_without_anything_being_edited(clean):
    await install("thing.one", name="Thing One")
    chart = await org.tree()
    assert find(chart["root"], "thing.one") is not None
    assert chart["counts"]["agents"] == 1


@pytest.mark.asyncio
async def test_reassigning_an_agent_moves_it_in_the_tree(clean):
    """The requirement in one test: reassignment must not need a redraw."""
    await install("boss.a", name="Boss A")
    await install("boss.b", name="Boss B")
    await install("worker", supervisor="boss.a")

    before = await org.tree()
    assert parent_of(before["root"], "worker")["id"] == "boss.a"

    await install("worker", supervisor="boss.b", version=2)

    after = await org.tree()
    assert parent_of(after["root"], "worker")["id"] == "boss.b", (
        "the chart did not follow the registry"
    )


@pytest.mark.asyncio
async def test_a_supervisor_nobody_registered_becomes_a_coordinator(clean):
    """media.director is a recipe, not an agent, and must be drawn as one."""
    await install("media.scout", domain="media", supervisor="media.director")

    chart = await org.tree()
    node = find(chart["root"], "media.director")

    assert node is not None, "the reporting line vanished"
    assert node["kind"] == "coordinator"
    assert node["name"] == "Media Director", "the domain is part of the name"
    assert [c["id"] for c in node["children"]] == ["media.scout"]
    # A coordinator is not an agent, and must not be counted as one.
    assert chart["counts"]["agents"] == 1


@pytest.mark.asyncio
async def test_a_registered_supervisor_is_used_as_itself(clean):
    """When the supervisor IS an agent, no phantom node is invented."""
    await install("lead.agent", name="Lead")
    await install("helper", supervisor="lead.agent")

    chart = await org.tree()
    lead = find(chart["root"], "lead.agent")
    assert lead["kind"] == "specialist"
    assert [c["id"] for c in lead["children"]] == ["helper"]


@pytest.mark.asyncio
async def test_an_agent_that_supervises_itself_still_appears(clean):
    """Bad data must be visible and odd, never invisible."""
    await install("loop.agent", supervisor="loop.agent")
    chart = await org.tree()
    assert parent_of(chart["root"], "loop.agent")["id"] == "jarvis"


@pytest.mark.asyncio
async def test_governance_is_a_rule_not_a_hard_coded_node(clean):
    """JARVIS Scientist does not exist yet.

    When it is registered it must be drawn differently without a line of
    code changing -- which is what makes this a living chart rather than a
    drawing with a gap left for a future feature.
    """
    empty = await org.tree()
    assert find(empty["root"], "scientist.experiments") is None, (
        "an agent that does not exist is on the chart"
    )

    await install("scientist.experiments", name="Scientist", domain="governance")
    chart = await org.tree()
    assert find(chart["root"], "scientist.experiments")["kind"] == "governance"


@pytest.mark.asyncio
async def test_agents_that_cannot_be_routed_are_still_part_of_the_organisation(clean):
    """A chart that showed only healthy agents would be a chart of the
    happy path, and the disabled one is exactly what you came to look at."""
    await install("live.one", status=Lifecycle.ACTIVE)
    await install("shelved", status=Lifecycle.DISABLED)
    await install("trying", status=Lifecycle.EXPERIMENTAL)
    await install("gone", status=Lifecycle.RETIRED)

    chart = await org.tree()
    shown = {c["id"] for c in chart["root"]["children"]}
    assert shown == {"live.one", "shelved", "trying", "gone"}
    assert chart["counts"]["disabled"] == 1
    assert chart["counts"]["experimental"] == 1


# --- what is happening right now -------------------------------------------

async def a_task(pool, capability, status, workflow_status="running"):
    workflow = await pool.fetchval(
        "INSERT INTO workflows (objective, requested_by, status) "
        "VALUES ('o','user:owner',$1) RETURNING id", workflow_status)
    return await pool.fetchval(
        "INSERT INTO tasks (workflow_id, objective, capability, status) "
        "VALUES ($1,'do it',$2,$3) RETURNING id", workflow, capability, status)


@pytest.mark.asyncio
async def test_a_running_task_shows_its_agent_as_working(clean):
    await install("busy.one")
    await a_task(clean, "busy.one", "running")

    chart = await org.tree()
    assert find(chart["root"], "busy.one")["state"] == "working"
    assert chart["counts"]["working"] == 1


@pytest.mark.asyncio
async def test_work_left_behind_by_a_dead_workflow_is_not_shown_as_queued(clean):
    """A failed workflow leaves its dependents blocked for ever.

    Counting those would show an agent as busy with work that will never
    run -- and "why has that been queued for three weeks" is a question
    with no answer, because nothing is wrong.
    """
    await install("idle.one")
    await a_task(clean, "idle.one", "blocked", workflow_status="failed")

    chart = await org.tree()
    assert find(chart["root"], "idle.one")["state"] == "idle"


# --- health refuses to answer on thin evidence -----------------------------

async def measure(pool, capability, successes):
    for value in successes:
        await pool.execute(
            "INSERT INTO agent_metrics (capability, metric, value) "
            "VALUES ($1,'success',$2)", capability, value)


@pytest.mark.asyncio
async def test_two_runs_and_a_failure_is_not_a_fifty_percent_failure_rate(clean):
    await install("new.one")
    await measure(clean, "new.one", [1, 0])

    chart = await org.tree()
    assert find(chart["root"], "new.one")["health"] == "unproven"


@pytest.mark.asyncio
async def test_a_run_of_failures_is_flagged(clean):
    await install("shaky")
    await measure(clean, "shaky", [0, 0, 1, 0, 0])

    chart = await org.tree()
    assert find(chart["root"], "shaky")["health"] == "watch"


@pytest.mark.asyncio
async def test_a_healthy_agent_says_so(clean):
    await install("solid")
    await measure(clean, "solid", [1, 1, 1, 1])
    chart = await org.tree()
    assert find(chart["root"], "solid")["health"] == "ok"


@pytest.mark.asyncio
async def test_a_disabled_agent_is_not_described_as_healthy(clean):
    await install("shelved", status=Lifecycle.DISABLED)
    await measure(clean, "shelved", [1, 1, 1, 1])
    chart = await org.tree()
    assert find(chart["root"], "shelved")["health"] == "off"


# --- the detail panel -------------------------------------------------------

@pytest.mark.asyncio
async def test_every_permission_is_accounted_for_either_way(clean):
    """Both halves. A list of what an agent holds says nothing about what
    it is stopped from doing, and "cannot publish" is the single most
    important fact about most of these."""
    await install("careful", permissions=frozenset({Permission.READ_MEMORY,
                                                    Permission.NETWORK}))
    detail = await org.detail("careful", SETTINGS)

    can = {p["permission"] for p in detail["permissions"]["can"]}
    cannot = {p["permission"] for p in detail["permissions"]["cannot"]}
    assert can == {"read_memory", "network"}
    assert len(can | cannot) == len(Permission), "a permission is unaccounted for"
    assert not (can & cannot)

    publish = next(p for p in detail["permissions"]["cannot"] if p["permission"] == "publish")
    assert publish["needs_approval"] is True
    agents = next(p for p in detail["permissions"]["cannot"] if p["permission"] == "modify_agents")
    assert agents["never_delegated"] is True


@pytest.mark.asyncio
async def test_an_agent_that_never_ran_reports_nothing_rather_than_zero(clean):
    await install("untried")
    detail = await org.detail("untried", SETTINGS)

    assert detail["performance"]["runs"] == 0
    assert detail["performance"]["success_rate"] is None, (
        "zero and unmeasured are opposite things"
    )
    assert detail["performance"]["enough_to_judge"] is False
    assert detail["economics"]["spend_per_task_inr"] is None
    assert detail["models"]["recent"] is None
    assert "correction rate" in detail["performance"]["not_measured"]


@pytest.mark.asyncio
async def test_the_two_cost_figures_are_reported_apart(clean):
    await install("spender")
    workflow = await clean.fetchval(
        "INSERT INTO workflows (objective, requested_by) VALUES ('o','user:owner') "
        "RETURNING id")
    await clean.execute(
        "INSERT INTO tasks (workflow_id, objective, capability, status, "
        "spend_inr, shadow_inr, finished_at) "
        "VALUES ($1,'x','spender','completed',0.50,3.00,now())", workflow)

    money = (await org.detail("spender", SETTINGS))["economics"]
    assert money["spend_30d_inr"] == 0.5
    assert money["shadow_30d_inr"] == 3.0
    assert money["spend_per_task_inr"] == 0.5
    assert "total_inr" not in money, "there is no combined figure, on purpose"


@pytest.mark.asyncio
async def test_a_tier_is_shown_with_the_model_it_means_today(clean):
    await install("thinker", model_tiers=(ModelTier.DEEP, ModelTier.STANDARD))
    models = (await org.detail("thinker", SETTINGS))["models"]

    assert models["tiers"] == ["deep", "standard"]
    assert models["default"]["tier"] == "deep"
    assert all(m["model"] for m in models["allowed"]), (
        "a tier with no model behind it tells the owner nothing"
    )


@pytest.mark.asyncio
async def test_a_running_task_is_shown_as_operational_state_only(clean):
    await install("busy.one")
    task_id = await a_task(clean, "busy.one", "running")
    await clean.execute("UPDATE tasks SET started_at = now() WHERE id = $1", task_id)

    activity = (await org.detail("busy.one", SETTINGS))["activity"]
    assert len(activity["current"]) == 1
    current = activity["current"][0]
    assert current["objective"] == "do it"
    assert current["task_id"] == str(task_id)
    assert current["workflow_id"]
    # Operational state, never what the agent was thinking.
    assert "reasoning" not in current and "context" not in current


@pytest.mark.asyncio
async def test_a_coordinator_reports_its_domain_rather_than_pretending_to_think(clean):
    await install("media.scout", domain="media", supervisor="media.director")
    await install("media.review", domain="media", supervisor="media.director")
    await a_task(clean, "media.scout", "running")

    detail = await org.detail("media.director", SETTINGS)
    assert detail["kind"] == "coordinator"
    assert detail["identity"]["routable"] is False
    assert "Not an agent" in detail["role"]
    assert detail["summary"]["agents"] == 2
    assert detail["summary"]["working"] == 1
    assert {r["id"] for r in detail["reports"]} == {"media.scout", "media.review"}


@pytest.mark.asyncio
async def test_a_coordinator_nobody_reports_to_does_not_exist(clean):
    assert await org.detail("nobody.director", SETTINGS) is None


@pytest.mark.asyncio
async def test_jarvis_is_the_orchestrator_not_an_agent(clean):
    await install("thing.one")
    detail = await org.detail("jarvis", SETTINGS)

    assert detail["kind"] == "orchestrator"
    assert detail["identity"]["routable"] is False
    assert detail["identity"]["capability"] is None
    assert detail["summary"]["agents"] == 1
    assert "not in the registry" in detail["role"]


@pytest.mark.asyncio
async def test_asking_about_something_that_does_not_exist_says_so(clean):
    assert await org.detail("no.such.thing", SETTINGS) is None


@pytest.mark.asyncio
async def test_the_max_spend_of_an_agent_is_shown_because_it_bounds_it(clean):
    await install("bounded", max_cost_inr=Decimal("6.00"))
    detail = await org.detail("bounded", SETTINGS)
    assert detail["limits"]["max_cost_inr"] == 6.0
    assert any("6.00" in r for r in detail["responsibilities"])
