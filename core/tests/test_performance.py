"""Section 15: the router learning which model is good at what.

Two things are being proved, and the second matters more than the first.

  1. The statistics are computed honestly -- windowed, grouped per model,
     and silent when there is not enough data to say anything.
  2. The statistics cannot do damage. They only ever escalate, they never
     beat an instruction, they never beat the budget, and a failure that
     was JARVIS's own is never recorded against a model.

Every guard below was written by breaking the thing it protects first and
watching the test fail. A guard that has never failed is a guard nobody
has checked.
"""
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio

from app.agents import model_router, performance, registry, runtime, tasks, telemetry
from app.agents.performance import ENOUGH, POOR, WINDOW_DAYS, Record
from app.agents.schemas import (
    AgentResult,
    AgentSpec,
    Lifecycle,
    ModelTier,
    Permission,
)
from app.config import Settings


WIPE = ("DELETE FROM agent_metrics; DELETE FROM agent_events; "
        "DELETE FROM tasks; DELETE FROM workflows; DELETE FROM agents;")


@pytest_asyncio.fixture
async def clean(db_pool):
    # Cleared on the way out as well as on the way in. These tests finish
    # tasks, and the briefing counts tasks finished in the last 24 hours:
    # leaving them behind fails a test in another file, which is a
    # miserable thing to debug from the failure message alone.
    await db_pool.execute(WIPE)
    registry._IMPLEMENTATIONS.clear()
    yield db_pool
    registry._IMPLEMENTATIONS.clear()
    await db_pool.execute(WIPE)


def settings() -> Settings:
    return Settings(llm_provider="gemini", model_cheap="flash-lite",
                    gemini_model="flash", model_deep="pro")


def record(model="flash", runs=10, successes=3, **kw) -> Record:
    return Record(provider=kw.pop("provider", "gemini"), model=model,
                  tier=kw.pop("tier", "standard"), runs=runs,
                  successes=successes, avg_latency_ms=kw.pop("latency", 900.0),
                  avg_cost_inr=kw.pop("cost", 0.4),
                  avg_confidence=kw.pop("confidence", 0.5))


async def metric(pool, capability, metric_name, value, *, provider="gemini",
                 model="flash", tier="standard", days_ago=0):
    """One measurement, written the way the runtime writes them."""
    await pool.execute(
        "INSERT INTO agent_metrics (capability, metric, value, provider, "
        "model, tier, created_at) "
        "VALUES ($1,$2,$3,$4,$5,$6, now() - ($7 || ' days')::interval)",
        capability, metric_name, Decimal(str(float(value))), provider, model,
        tier, str(days_ago),
    )


async def runs(pool, capability, *, model="flash", provider="gemini",
               wins=0, losses=0, days_ago=0):
    for _ in range(wins):
        await metric(pool, capability, "success", 1, model=model,
                     provider=provider, days_ago=days_ago)
    for _ in range(losses):
        await metric(pool, capability, "success", 0, model=model,
                     provider=provider, days_ago=days_ago)


# --- the arithmetic -------------------------------------------------------

def test_success_rate_is_wins_over_runs():
    assert record(runs=10, successes=3).success_rate == pytest.approx(0.3)


def test_no_runs_is_zero_rather_than_a_crash():
    """A model with nothing recorded must not divide by zero."""
    assert record(runs=0, successes=0).success_rate == 0.0


def test_enough_is_a_floor_not_a_ceiling():
    assert record(runs=ENOUGH - 1).enough is False
    assert record(runs=ENOUGH).enough is True


# --- what the router does with it -----------------------------------------

def test_without_measurements_the_router_is_unchanged():
    plain = model_router.choose(settings(), tier=ModelTier.CHEAP)
    assert plain.tier is ModelTier.CHEAP
    assert "raised to" not in plain.reason


def test_a_struggling_model_raises_cheap_to_standard():
    choice = model_router.choose(settings(), tier=ModelTier.CHEAP,
                                 measured=record(model="flash-lite",
                                                 runs=12, successes=3))
    assert choice.tier is ModelTier.STANDARD
    # The reason has to carry the evidence, or "why did this cost more"
    # has no answer better than "the router decided".
    assert "flash-lite" in choice.reason
    assert "25%" in choice.reason
    assert "12 runs" in choice.reason


def test_a_struggling_model_raises_standard_to_deep():
    choice = model_router.choose(settings(), tier=ModelTier.STANDARD,
                                 measured=record())
    assert choice.tier is ModelTier.DEEP
    assert choice.model == "pro"


def test_measurements_never_lower_a_tier():
    """The one direction this is allowed to move.

    "This model has been failing here" is measurable. "That model would
    be better" is not -- nothing has tried them on comparable work. A
    router that demoted on this data would be confidently wrong.
    """
    choice = model_router.choose(settings(), tier=ModelTier.DEEP,
                                 measured=record(model="pro", runs=40,
                                                 successes=1))
    assert choice.tier is ModelTier.DEEP, "measurements must not demote"
    assert "raised to" not in choice.reason


def test_an_agent_is_not_escalated_past_what_it_is_registered_for():
    """The escalation is a suggestion; the registry is a boundary."""
    choice = model_router.choose(
        settings(), tier=ModelTier.CHEAP, allowed=(ModelTier.CHEAP,),
        measured=record(model="flash-lite"),
    )
    assert choice.tier is ModelTier.CHEAP
    assert "not registered" in choice.reason


def test_an_empty_budget_still_beats_the_measurements():
    choice = model_router.choose(
        settings(), tier=ModelTier.STANDARD, budget_left=Decimal("0.2"),
        measured=record(),
    )
    assert choice.tier is ModelTier.STANDARD, "escalated past an empty budget"
    assert "little budget left" in choice.reason


# --- reading the history --------------------------------------------------

@pytest.mark.asyncio
async def test_nothing_measured_says_so(clean):
    assert await performance.history("general.writer") == []
    said = (await performance.report("general.writer"))["said"]
    assert said == "Nothing has been measured yet."


@pytest.mark.asyncio
async def test_history_groups_by_model(clean):
    await runs(clean, "general.writer", model="flash", wins=8, losses=2)
    await runs(clean, "general.writer", model="pro", wins=5, losses=0)

    by_model = {r.model: r for r in await performance.history("general.writer")}
    assert set(by_model) == {"flash", "pro"}
    assert by_model["flash"].runs == 10
    assert by_model["flash"].success_rate == pytest.approx(0.8)
    assert by_model["pro"].runs == 5


@pytest.mark.asyncio
async def test_rows_that_do_not_know_who_ran_are_left_out(clean):
    """Every row written before migration 011 has a NULL model.

    Counting those as a model called "unknown" would invent a track
    record out of rows that never recorded one.
    """
    await clean.execute(
        "INSERT INTO agent_metrics (capability, metric, value) "
        "VALUES ('general.writer', 'success', 0)"
    )
    assert await performance.history("general.writer") == []


@pytest.mark.asyncio
async def test_only_the_recent_window_counts(clean):
    """A model that was bad in March and is good now is good now."""
    await runs(clean, "general.writer", wins=0, losses=20,
               days_ago=WINDOW_DAYS + 5)
    await runs(clean, "general.writer", wins=10, losses=0)

    (only,) = await performance.history("general.writer")
    assert only.runs == 10, "old failures were still being counted"
    assert only.success_rate == 1.0


@pytest.mark.asyncio
async def test_history_is_scoped_to_the_capability(clean):
    await runs(clean, "general.writer", wins=10, losses=0)
    await runs(clean, "general.coder", wins=0, losses=10)

    (writer,) = await performance.history("general.writer")
    assert writer.success_rate == 1.0


@pytest.mark.asyncio
async def test_a_broken_statistics_table_does_not_stop_the_router(monkeypatch):
    """Never raises. A router that cannot route because a statistics
    query was slow is worse than a router that does not learn."""
    async def boom(*a, **kw):
        raise RuntimeError("relation does not exist")

    monkeypatch.setattr(performance, "fetch", boom)
    assert await performance.history("general.writer") == []
    assert await performance.struggling("general.writer") is None


# --- who is struggling ----------------------------------------------------

@pytest.mark.asyncio
async def test_a_handful_of_runs_is_not_evidence(clean):
    """Two failures out of two is a hundred per cent and means nothing."""
    await runs(clean, "general.writer", wins=0, losses=ENOUGH - 1)
    assert await performance.struggling("general.writer") is None


@pytest.mark.asyncio
async def test_a_model_that_is_doing_fine_is_not_flagged(clean):
    await runs(clean, "general.writer", wins=9, losses=1)
    assert await performance.struggling("general.writer") is None


@pytest.mark.asyncio
async def test_a_model_that_is_failing_is_flagged(clean):
    await runs(clean, "general.writer", wins=2, losses=8)
    worst = await performance.struggling("general.writer")
    assert worst is not None
    assert worst.model == "flash"
    assert worst.success_rate < POOR


@pytest.mark.asyncio
async def test_the_worst_is_returned_not_the_first(clean):
    """What the router does with this is escalate away from it, so the
    answer has to be the one being escalated away from."""
    await runs(clean, "general.writer", model="good", wins=10, losses=0)
    await runs(clean, "general.writer", model="bad", wins=1, losses=9)

    worst = await performance.struggling("general.writer")
    assert worst is not None and worst.model == "bad"


@pytest.mark.asyncio
async def test_struggling_ignores_other_capabilities(clean):
    await runs(clean, "general.coder", wins=0, losses=20)
    assert await performance.struggling("general.writer") is None


@pytest.mark.asyncio
async def test_the_report_says_when_it_cannot_judge(clean):
    await runs(clean, "general.writer", wins=1, losses=1)
    said = (await performance.report("general.writer"))["said"]
    assert f"none with the {ENOUGH} runs needed" in said


# --- wired into the one execution path ------------------------------------
#
# The statistics being right is half of it. The other half is that
# `run_task` actually consults them, and that what it writes back is
# honest about who did what -- otherwise the router is learning from its
# own mistakes.

async def install(capability, fn=None, **kw):
    spec = AgentSpec(
        capability=capability, name=capability, task_types=("general",),
        permissions=frozenset({Permission.READ_MEMORY}),
        model_tiers=kw.pop("model_tiers", (ModelTier.STANDARD, ModelTier.DEEP)),
        status=Lifecycle.ACTIVE, **kw,
    )
    await registry.register(spec)
    if fn is not None:
        registry.implement(capability, fn)
    return spec


async def works(handoff, choice):
    return AgentResult(output="done", tokens_in=100, tokens_out=50,
                       confidence=0.9)


async def breaks(handoff, choice):
    raise RuntimeError("the model returned nonsense")


async def one_task(capability, **kw):
    wf = await tasks.create_workflow("A workflow", "user:owner")
    task_id = await tasks.create(objective="Do the thing", capability=capability,
                                 workflow_id=wf, **kw)
    ok = await runtime.run_task(task_id)
    return wf, task_id, ok


async def measurements(pool, task_id):
    return await pool.fetch(
        "SELECT metric, provider, model, tier FROM agent_metrics "
        "WHERE task_id = $1", task_id)


@pytest.mark.asyncio
async def test_a_finished_run_records_who_ran_it(clean):
    await install("general.writer", works)
    _, task_id, ok = await one_task("general.writer")
    assert ok

    rows = await measurements(clean, task_id)
    assert rows, "a completed task recorded no measurements at all"
    for row in rows:
        assert row["model"], f"'{row['metric']}' was recorded with no model"
        assert row["provider"]
        assert row["tier"] == "standard"


@pytest.mark.asyncio
async def test_a_failure_the_model_caused_is_recorded_against_it(clean):
    await install("general.writer", breaks)
    _, task_id, ok = await one_task("general.writer")
    assert ok is False

    rows = {r["metric"]: r for r in await measurements(clean, task_id)}
    assert rows["failure"]["model"], "the model was handed the work and is not named"


@pytest.mark.asyncio
async def test_jarvis_own_failures_are_not_blamed_on_a_model(clean):
    """A capability registered with nothing loaded to run it.

    The model was chosen and then never asked for anything. Recording
    that against it would teach the router to escalate away from JARVIS's
    own deployment bugs -- which no model can fix, and which would go on
    costing more money every time they happened.
    """
    await install("general.writer")  # registered, no implementation
    _, task_id, ok = await one_task("general.writer")
    assert ok is False

    rows = {r["metric"]: r for r in await measurements(clean, task_id)}
    assert rows, "the failure was not recorded at all"
    assert rows["failure"]["model"] is None, (
        "a failure that happened before the model was asked anything "
        "was recorded as that model's failure"
    )


@pytest.mark.asyncio
async def test_a_refusal_is_recorded_but_blames_nobody(clean):
    await install("general.writer", works)
    _, task_id, ok = await one_task("general.writer",
                                    constraints={"permissions": ["publish"]})
    assert ok is False

    rows = {r["metric"]: r for r in await measurements(clean, task_id)}
    assert "refused" in rows, "a refusal must still be measured"
    assert rows["refused"]["model"] is None
    assert rows["refused"]["provider"] is None


@pytest.mark.asyncio
async def test_run_task_escalates_away_from_a_model_that_is_failing(clean):
    """End to end: measurements in, a different tier out."""
    await runs(clean, "general.writer", wins=2, losses=10)
    await install("general.writer", works)

    wf, _, ok = await one_task("general.writer")
    assert ok

    routed = next(e for e in await telemetry.trace(wf)
                  if e["kind"] == "model_routed")
    assert routed["detail"]["tier"] == "deep", (
        "the router ignored a model failing 83% of the time here"
    )
    assert "raised to deep" in routed["detail"]["why"]
    # And it has to say what it learned from, not just that it learned.
    assert routed["detail"]["measured"]["runs"] == 12


@pytest.mark.asyncio
async def test_a_named_model_is_not_second_guessed(clean):
    """The owner naming a model is an instruction, not a datapoint.

    A success rate quietly overruling it would be exactly the silent
    substitution the preference system exists to prevent.
    """
    await runs(clean, "general.writer", wins=2, losses=10)
    await install("general.writer", works)

    wf, _, ok = await one_task("general.writer",
                               constraints={"model": "flash"})
    assert ok

    routed = next(e for e in await telemetry.trace(wf)
                  if e["kind"] == "model_routed")
    assert routed["detail"]["measured"] is None
    assert "raised to" not in routed["detail"]["why"], (
        "a measurement overruled a model the owner named"
    )


# --- one judgement, not two ----------------------------------------------

def test_worst_of_is_the_worst_not_the_first():
    """The dashboard panel had its own copy of this for about an hour.

    Its version took the first poor model out of a list sorted by success
    rate -- which is the BEST of the poor ones, not the worst. So the
    panel could name one model while the router escalated away from
    another. Both now call this.
    """
    from app.agents.performance import worst_of

    records = [record(model="ok", runs=20, successes=19),
               record(model="poor", runs=20, successes=10),
               record(model="awful", runs=20, successes=2)]
    assert worst_of(records).model == "awful"
    assert worst_of(sorted(records, key=lambda r: -r.success_rate)).model == "awful"


def test_worst_of_ignores_models_it_cannot_judge():
    from app.agents.performance import worst_of

    records = [record(model="judged", runs=20, successes=4),
               record(model="barely tried", runs=2, successes=0)]
    assert worst_of(records).model == "judged", (
        "a model with two runs was picked as the one to route around"
    )


@pytest.mark.asyncio
async def test_the_org_panel_and_the_router_name_the_same_model(clean):
    from app.agents import org

    await runs(clean, "general.writer", model="ok", wins=10, losses=0)
    await runs(clean, "general.writer", model="poor", wins=4, losses=6)
    await runs(clean, "general.writer", model="awful", wins=1, losses=11)
    await runs(clean, "general.writer", model="barely tried", wins=0, losses=2)

    panel = await org._measured_models("general.writer")
    router = await performance.struggling("general.writer")

    assert router is not None
    assert panel["struggling"] == router.model == "awful"
    assert {r["model"] for r in panel["rows"]} == {
        "ok", "poor", "awful", "barely tried"}
    thin = next(r for r in panel["rows"] if r["model"] == "barely tried")
    assert thin["enough_to_judge"] is False, (
        "the panel would let the owner read a rate off two runs"
    )
