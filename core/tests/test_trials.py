"""Candidate -> benchmark -> promotion (section 20).

The registry has kept version history from the start, and it will stand
one version down and another up on request. What never existed is the
middle word, and without it "promotion" means somebody thought the new
one looked better.

Most of this file is about the ways a benchmark can lie to you:

- two small numbers subtracted, where the noise in the difference is
  bigger than the noise in either side;
- a retry sending a candidate's failure to the baseline, so the thing
  meant to measure the failure hides it;
- a candidate that is not a candidate but a permission it did not have
  before, arriving through a door marked "we are just trying this".
"""
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

from app.agents import registry, runtime, tasks, telemetry, trials
from app.agents.schemas import (
    AgentResult,
    AgentSpec,
    Lifecycle,
    ModelTier,
    Permission,
)
from app.agents.trials import (
    CANNOT_SAY,
    ENOUGH_EACH,
    MARGIN,
    PROMOTE,
    REJECT,
    WATCH,
    Arm,
    CannotTrial,
)


@pytest_asyncio.fixture
async def clean(db_pool):
    await db_pool.execute(
        "DELETE FROM agent_trials; DELETE FROM agent_metrics; "
        "DELETE FROM agent_events; DELETE FROM tasks; DELETE FROM workflows; "
        "DELETE FROM agents;"
    )
    registry._IMPLEMENTATIONS.clear()
    yield db_pool
    registry._IMPLEMENTATIONS.clear()
    await db_pool.execute(
        "DELETE FROM agent_trials; DELETE FROM agent_metrics; "
        "DELETE FROM agent_events; DELETE FROM tasks; DELETE FROM workflows; "
        "DELETE FROM agents;"
    )


def spec(capability, version, **kw) -> AgentSpec:
    return AgentSpec(
        capability=capability, name=f"{capability} v{version}", version=version,
        task_types=("general",),
        permissions=kw.pop("permissions", frozenset({Permission.READ_MEMORY})),
        model_tiers=(ModelTier.STANDARD,),
        status=kw.pop("status", Lifecycle.EXPERIMENTAL),
        **kw,
    )


async def two_versions(capability="general.writer", **kw):
    await registry.register(spec(capability, 1, status=Lifecycle.ACTIVE))
    await registry.register(spec(capability, 2, **kw))
    return capability


def arm(version=2, runs=20, successes=15) -> Arm:
    return Arm(version=version, agent_id=str(uuid4()), runs=runs,
               successes=successes, avg_confidence=0.8, avg_cost_inr=0.4,
               avg_latency_ms=900.0)


# --- the judgement, on its own --------------------------------------------

def test_a_clear_win_is_a_promotion():
    call = trials.judge(arm(2, 20, 18), arm(1, 20, 10))
    assert call.call == PROMOTE
    assert "90%" in call.said and "50%" in call.said


def test_a_clear_loss_is_a_rejection():
    call = trials.judge(arm(2, 20, 8), arm(1, 20, 18))
    assert call.call == REJECT
    assert "taking a share of real work" in call.said


def test_a_difference_inside_the_noise_is_not_a_difference():
    """The one that would quietly promote randomness.

    Nineteen out of twenty against eighteen out of twenty is a five
    per cent gap on forty runs. Promote on that and you are promoting
    a coin landing the same way twice.
    """
    call = trials.judge(arm(2, 20, 19), arm(1, 20, 18))
    assert call.call == WATCH
    assert "inside the noise" in call.said
    assert call.difference < MARGIN


def test_exactly_the_margin_counts_as_a_win():
    """Stated, because a boundary nobody wrote down is a boundary that
    moves whenever the code is next edited."""
    call = trials.judge(arm(2, 100, 60), arm(1, 100, 50))
    assert call.difference == pytest.approx(MARGIN)
    assert call.call == PROMOTE


def test_too_few_runs_is_its_own_answer():
    for candidate, baseline in (
        (arm(2, ENOUGH_EACH - 1, ENOUGH_EACH - 1), arm(1, 50, 5)),
        (arm(2, 50, 50), arm(1, ENOUGH_EACH - 1, 0)),
    ):
        call = trials.judge(candidate, baseline)
        assert call.call == CANNOT_SAY
        assert str(ENOUGH_EACH) in call.said


def test_a_perfect_record_on_two_runs_promotes_nothing():
    """Two out of two is a hundred per cent and means nothing."""
    assert trials.judge(arm(2, 2, 2), arm(1, 2, 0)).call == CANNOT_SAY


# --- which arm a task lands on --------------------------------------------

def test_the_same_task_always_lands_on_the_same_arm():
    """The point of not using a coin.

    A random split would send a candidate's failure to the baseline on
    the retry, the baseline would succeed, and the failure would be
    hidden by the thing meant to measure it.
    """
    trial = {"share": Decimal("0.5")}
    task_id = uuid4()
    first = trials.takes_candidate(trial, task_id)
    for _ in range(50):
        assert trials.takes_candidate(trial, task_id) is first


def test_the_split_is_roughly_the_share_asked_for():
    trial = {"share": Decimal("0.20")}
    ids = [UUID(int=i) for i in range(2000)]
    share = sum(trials.takes_candidate(trial, i) for i in ids) / len(ids)
    assert 0.15 < share < 0.25, f"asked for 20%, got {share:.0%}"


def test_a_bigger_share_sends_more_work():
    ids = [UUID(int=i) for i in range(2000)]
    small = sum(trials.takes_candidate({"share": Decimal("0.1")}, i) for i in ids)
    large = sum(trials.takes_candidate({"share": Decimal("0.5")}, i) for i in ids)
    assert large > small * 3


def test_with_no_trial_nothing_goes_to_a_candidate():
    assert trials.takes_candidate(None, uuid4()) is False
    assert trials.takes_candidate({}, uuid4()) is False


# --- starting one ----------------------------------------------------------

@pytest.mark.asyncio
async def test_a_trial_puts_the_candidate_where_it_can_be_asked_for(clean):
    capability = await two_versions()
    await trials.start(capability, 2, by="user:owner")

    candidate = await registry.get(capability, 2)
    assert candidate.status is Lifecycle.TESTING
    # And the live one is untouched: a trial is not a deployment.
    assert (await registry.resolve(capability)).version == 1


@pytest.mark.asyncio
async def test_a_candidate_may_not_hold_more_than_the_version_it_would_replace(clean):
    """The security one.

    A candidate that may do more is not a candidate. It is a privilege
    escalation with a version number, arriving through a door marked
    "we are just trying this".
    """
    capability = await two_versions(permissions=frozenset({
        Permission.READ_MEMORY, Permission.PUBLISH}))

    with pytest.raises(CannotTrial, match="publish"):
        await trials.start(capability, 2, by="user:owner")
    assert (await registry.get(capability, 2)).status is Lifecycle.EXPERIMENTAL


@pytest.mark.asyncio
async def test_a_candidate_holding_less_is_fine(clean):
    capability = await two_versions(permissions=frozenset())
    await trials.start(capability, 2, by="user:owner")
    assert await trials.running(capability)


@pytest.mark.asyncio
async def test_only_one_trial_at_a_time(clean):
    capability = await two_versions()
    await registry.register(spec(capability, 3))
    await trials.start(capability, 2, by="user:owner")

    with pytest.raises(CannotTrial, match="already has a trial"):
        await trials.start(capability, 3, by="user:owner")


@pytest.mark.asyncio
async def test_a_trial_cannot_send_most_of_the_work_to_the_unproven_one(clean):
    capability = await two_versions()
    for too_much in (Decimal("0.6"), Decimal("1"), Decimal("0"), Decimal("-0.1")):
        with pytest.raises(CannotTrial, match="deployment, not a trial|above 0"):
            await trials.start(capability, 2, by="user:owner", share=too_much)


@pytest.mark.asyncio
async def test_trialling_the_version_already_live_is_refused(clean):
    capability = await two_versions()
    with pytest.raises(CannotTrial, match="already doing the job"):
        await trials.start(capability, 1, by="user:owner")


@pytest.mark.asyncio
async def test_a_version_that_does_not_exist_is_refused(clean):
    capability = await two_versions()
    with pytest.raises(CannotTrial, match="no version 9"):
        await trials.start(capability, 9, by="user:owner")


@pytest.mark.asyncio
async def test_with_nothing_live_there_is_nothing_to_compare_against(clean):
    await registry.register(spec("general.writer", 1))  # experimental
    with pytest.raises(CannotTrial, match="nothing to compare"):
        await trials.start("general.writer", 1, by="user:owner")


def test_the_margin_is_not_at_the_mercy_of_binary_arithmetic():
    """60% minus 50% is 0.09999999999999998.

    Which comparisons that bites depends on the run counts, so a
    threshold implemented without rounding looks, from outside, like a
    threshold that wanders.
    """
    # All at or above the run floor, or the answer is "not enough yet"
    # and the test proves nothing about the margin.
    for wins, of, base_wins in ((60, 100, 50), (30, 50, 25), (12, 20, 10),
                                (21, 30, 18)):
        call = trials.judge(arm(2, of, wins), arm(1, of, base_wins))
        assert call.call == PROMOTE, (
            f"{wins}/{of} against {base_wins}/{of} is a "
            f"{(wins - base_wins) / of:.0%} gap and was not counted as one"
        )


def test_the_number_shown_is_the_number_decided_on():
    call = trials.judge(arm(2, 100, 60), arm(1, 100, 50))
    assert call.difference == call.as_detail()["difference"]
    assert call.difference >= MARGIN


# --- the measurement, end to end ------------------------------------------

async def measured(pool, capability, version, *, wins, losses):
    """Runs attributed to one version, the way the runtime attributes them."""
    from app.db import execute

    agent_id = (await registry.get(capability, version)).id
    for value, n in ((1, wins), (0, losses)):
        for _ in range(n):
            await execute(
                "INSERT INTO agent_metrics (capability, agent_id, metric, value) "
                "VALUES ($1,$2,'success',$3)",
                capability, agent_id, Decimal(str(value)))


@pytest.mark.asyncio
async def test_the_verdict_reads_real_measurements(clean):
    capability = await two_versions()
    await trials.start(capability, 2, by="user:owner")
    await measured(clean, capability, 2, wins=18, losses=2)
    await measured(clean, capability, 1, wins=10, losses=10)

    call = await trials.verdict(capability)
    assert call.call == PROMOTE
    assert call.candidate.runs == 20 and call.baseline.runs == 20
    assert call.candidate.success_rate == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_runs_from_before_the_trial_do_not_count(clean):
    """Otherwise the baseline arrives with a year of history and the
    candidate with none, and the comparison is not a comparison."""
    capability = await two_versions()
    await measured(clean, capability, 1, wins=0, losses=40)  # before
    await trials.start(capability, 2, by="user:owner")
    await measured(clean, capability, 1, wins=18, losses=2)  # during
    await measured(clean, capability, 2, wins=18, losses=2)

    call = await trials.verdict(capability)
    assert call.baseline.runs == 20, "measurements from before the trial counted"
    assert call.call == WATCH


@pytest.mark.asyncio
async def test_no_trial_means_no_verdict(clean):
    await two_versions()
    assert await trials.verdict("general.writer") is None


# --- deciding --------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_winning_candidate_can_be_promoted(clean):
    capability = await two_versions()
    await trials.start(capability, 2, by="user:owner")
    await measured(clean, capability, 2, wins=18, losses=2)
    await measured(clean, capability, 1, wins=10, losses=10)

    detail = await trials.promote(capability, by="user:owner")

    assert (await registry.resolve(capability)).version == 2
    assert (await registry.get(capability, 1)).status is Lifecycle.DISABLED
    assert await trials.running(capability) is None
    # The numbers are kept WITH the decision, or a rollback later has
    # nothing to reason about.
    assert detail["candidate"]["runs"] == 20
    assert detail["forced"] is False


@pytest.mark.asyncio
async def test_a_candidate_the_numbers_do_not_back_is_not_promoted(clean):
    capability = await two_versions()
    await trials.start(capability, 2, by="user:owner")
    await measured(clean, capability, 2, wins=13, losses=7)
    await measured(clean, capability, 1, wins=12, losses=8)

    with pytest.raises(CannotTrial, match="do not support promoting"):
        await trials.promote(capability, by="user:owner")

    assert (await registry.resolve(capability)).version == 1
    assert await trials.running(capability), "the trial was closed anyway"


@pytest.mark.asyncio
async def test_too_few_runs_is_not_a_reason_to_promote(clean):
    capability = await two_versions()
    await trials.start(capability, 2, by="user:owner")
    await measured(clean, capability, 2, wins=3, losses=0)

    with pytest.raises(CannotTrial):
        await trials.promote(capability, by="user:owner")


@pytest.mark.asyncio
async def test_the_owner_can_overrule_the_numbers_and_it_says_so(clean):
    """His to do. Recorded as overruling, not as a promotion the data
    supported -- the difference matters when somebody reads this back."""
    capability = await two_versions()
    await trials.start(capability, 2, by="user:owner")
    await measured(clean, capability, 2, wins=1, losses=19)
    await measured(clean, capability, 1, wins=19, losses=1)

    detail = await trials.promote(capability, by="user:owner", force=True)

    assert (await registry.resolve(capability)).version == 2
    assert detail["forced"] is True
    assert detail["call"] == REJECT, (
        "a forced promotion recorded the verdict as if it had agreed"
    )


@pytest.mark.asyncio
async def test_a_rejected_candidate_is_kept_for_comparison(clean):
    capability = await two_versions()
    await trials.start(capability, 2, by="user:owner")
    await measured(clean, capability, 2, wins=2, losses=18)
    await measured(clean, capability, 1, wins=18, losses=2)

    await trials.reject(capability, by="user:owner", reason="worse")

    assert (await registry.get(capability, 2)).status is Lifecycle.DISABLED
    assert (await registry.resolve(capability)).version == 1
    assert await trials.running(capability) is None
    # Disabled, not retired: versions are never edited in place precisely
    # so the old one is still there to compare against.
    assert len(await registry.versions(capability)) == 2


@pytest.mark.asyncio
async def test_a_trial_can_be_stopped_without_judging_it(clean):
    capability = await two_versions()
    await trials.start(capability, 2, by="user:owner")
    await trials.abandon(capability, by="user:owner", reason="changed my mind")

    assert await trials.running(capability) is None
    assert (await registry.get(capability, 2)).status is Lifecycle.TESTING
    assert (await registry.resolve(capability)).version == 1


@pytest.mark.asyncio
async def test_deciding_a_trial_that_is_not_running_is_refused(clean):
    await two_versions()
    for call in (trials.promote, trials.reject, trials.abandon):
        with pytest.raises(CannotTrial, match="no trial running"):
            await call("general.writer", by="user:owner")


@pytest.mark.asyncio
async def test_a_finished_trial_is_on_the_record(clean):
    capability = await two_versions()
    await trials.start(capability, 2, by="user:owner")
    await measured(clean, capability, 2, wins=18, losses=2)
    await measured(clean, capability, 1, wins=10, losses=10)
    await trials.promote(capability, by="user:owner")

    (row,) = await trials.history(capability)
    assert row["status"] == "promoted"
    assert row["decided_by"] == "user:owner"
    assert row["ended_at"] is not None
    assert row["verdict"]["candidate"]["success_rate"] == 0.9


# --- the runtime actually splitting the traffic ---------------------------

async def works(handoff, choice):
    return AgentResult(output="done", tokens_in=10, tokens_out=5, confidence=0.9)


@pytest.mark.asyncio
async def test_a_trial_sends_some_tasks_to_the_candidate_and_some_not(clean):
    capability = await two_versions()
    registry.implement(capability, works)
    await trials.start(capability, 2, by="user:owner", share=Decimal("0.5"))

    wf = await tasks.create_workflow("A batch", "user:owner")
    versions = []
    for _ in range(30):
        task_id = await tasks.create(objective="do it", capability=capability,
                                     workflow_id=wf)
        assert await runtime.run_task(task_id)
        selected = next(e for e in await telemetry.trace(wf)
                        if e["kind"] == "agent_selected"
                        and e["task_id"] == task_id)
        versions.append(selected["detail"]["version"])

    assert set(versions) == {1, 2}, f"the trial did not split: {set(versions)}"
    # And the trace says WHY, or a run on a candidate is indistinguishable
    # from the live version having changed under you.
    on_candidate = next(e for e in await telemetry.trace(wf)
                        if e["kind"] == "agent_selected"
                        and e["detail"]["version"] == 2)
    assert "being trialled" in on_candidate["detail"]["why"]
    assert on_candidate["detail"]["baseline_version"] == 1


@pytest.mark.asyncio
async def test_with_no_trial_every_task_goes_to_the_live_version(clean):
    capability = await two_versions()
    registry.implement(capability, works)

    wf = await tasks.create_workflow("A batch", "user:owner")
    for _ in range(10):
        task_id = await tasks.create(objective="do it", capability=capability,
                                     workflow_id=wf)
        assert await runtime.run_task(task_id)

    versions = {e["detail"]["version"] for e in await telemetry.trace(wf)
                if e["kind"] == "agent_selected"}
    assert versions == {1}


@pytest.mark.asyncio
async def test_a_candidate_run_is_measured_against_the_candidate(clean):
    """The whole benchmark rests on this.

    If a candidate's runs were attributed to the live version, the trial
    would compare the baseline against itself and always find no
    difference.
    """
    capability = await two_versions()
    registry.implement(capability, works)
    await trials.start(capability, 2, by="user:owner", share=Decimal("0.5"))

    wf = await tasks.create_workflow("A batch", "user:owner")
    for _ in range(30):
        task_id = await tasks.create(objective="do it", capability=capability,
                                     workflow_id=wf)
        await runtime.run_task(task_id)

    call = await trials.verdict(capability)
    assert call.candidate.runs > 0, "no run was attributed to the candidate"
    assert call.baseline.runs > 0
    assert call.candidate.runs + call.baseline.runs == 30


def test_nothing_can_promote_itself():
    """Two guards, because one of them is a promise and the other is code.

    `promote` reads the verdict, and only something outside calls it --
    the owner's tap, or the Governor within its ceiling. And no agent may
    hold the permission that would let it change the registry directly,
    whatever an agent concludes about its own successor.
    """
    from app.agents.schemas import NEVER_DELEGATED, Permission

    assert Permission.MODIFY_AGENTS in NEVER_DELEGATED

    from app.constitution import is_protected

    why = is_protected("core/app/agents/trials.py")
    assert why, "self-development can rewrite how promotion works"
    assert "replaces the live one" in why


@pytest.mark.asyncio
async def test_the_state_reads_as_plain_words(clean):
    capability = await two_versions()
    assert "Nothing is being trialled" in (await trials.state())["said"]
    assert "No trial running" in (await trials.state(capability))["said"]

    await trials.start(capability, 2, by="user:owner")
    await measured(clean, capability, 2, wins=3, losses=0)

    said = (await trials.state(capability))["said"]
    assert str(ENOUGH_EACH) in said, f"does not say what it is waiting for: {said}"
    assert (await trials.state(capability))["running"]["candidate_version"] == 2


@pytest.mark.asyncio
async def test_asking_whether_anything_is_being_tried_finds_it(clean):
    """The dashboard's question.

    It asks without knowing which capability, and an answer of "nothing"
    to that made a running trial invisible on the one screen built to
    show it.
    """
    capability = await two_versions()
    await trials.start(capability, 2, by="user:owner")

    found = await trials.running()
    assert found is not None and found["capability"] == capability

    state = await trials.state()
    assert state["running"] is not None
    assert state["running"]["capability"] == capability
    assert state["said"] != "Nothing is being trialled."


@pytest.mark.asyncio
async def test_with_genuinely_nothing_running_it_says_so(clean):
    await two_versions()
    assert await trials.running() is None
    assert (await trials.state())["said"] == "Nothing is being trialled."


@pytest.mark.asyncio
async def test_the_verdict_without_a_capability_is_about_the_trial_found(clean):
    capability = await two_versions()
    await trials.start(capability, 2, by="user:owner")
    await measured(clean, capability, 2, wins=18, losses=2)
    await measured(clean, capability, 1, wins=10, losses=10)

    call = await trials.verdict()
    assert call is not None and call.call == PROMOTE
    assert call.candidate.runs == 20, (
        "it judged the wrong versions when told no capability"
    )


@pytest.mark.asyncio
async def test_the_org_panel_is_about_the_version_doing_the_job(clean):
    """Click an agent in the tree, get a panel about THAT agent.

    The tree is built from routable agents; the detail used to be built
    from the highest version number. The moment anything had a candidate
    registered for testing, those were different agents -- with different
    permissions and different numbers -- and nothing said so.
    """
    from app.agents import org
    from app.config import Settings

    capability = await two_versions(permissions=frozenset())
    await trials.start(capability, 2, by="user:owner")

    panel = await org.detail(capability, Settings())
    assert panel["identity"]["version"] == 1, (
        "the panel is about the candidate, not the version doing the work")
    assert panel["identity"]["lifecycle"] == "active"

    # And the candidate is not hidden -- it is just not mistaken for the
    # live one.
    assert panel["trial"]["candidate_version"] == 2
    assert panel["trial"]["baseline_version"] == 1
    assert panel["trial"]["said"]


@pytest.mark.asyncio
async def test_with_no_trial_the_panel_says_nothing_about_one(clean):
    from app.agents import org
    from app.config import Settings

    capability = await two_versions()
    assert (await org.detail(capability, Settings()))["trial"] is None


@pytest.mark.asyncio
async def test_a_capability_with_nothing_live_still_has_a_panel(clean):
    """Disabled and experimental versions are still worth reading about."""
    from app.agents import org
    from app.config import Settings

    await registry.register(spec("general.writer", 1, status=Lifecycle.DISABLED))
    panel = await org.detail("general.writer", Settings())
    assert panel is not None
    assert panel["identity"]["version"] == 1
    assert panel["identity"]["routable"] is False
