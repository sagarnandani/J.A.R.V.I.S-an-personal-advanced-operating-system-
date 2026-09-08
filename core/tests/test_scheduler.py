"""Work that happens without being asked.

This is the first code in the project that spends the owner's money with
nobody watching, so most of what is tested here is restraint: that it
stops well short of the ceiling, that a schedule cannot run away, that
two instances cannot both take the same job, and that being late does not
mean running four times to catch up.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio

from app import scheduler
from app.agents import builtin, registry
from app.config import Settings

SETTINGS = Settings(scheduler_enabled=True, monthly_budget_inr=Decimal("3500"),
                    scheduler_budget_percent=60, timezone="Asia/Kolkata")


@pytest_asyncio.fixture
async def clean(db_pool):
    await db_pool.execute(
        "DELETE FROM schedules; DELETE FROM agent_metrics; DELETE FROM agent_events; "
        "DELETE FROM tasks; DELETE FROM workflows; DELETE FROM agents; "
        "DELETE FROM audit_log;"
    )
    registry._IMPLEMENTATIONS.clear()
    yield db_pool
    registry._IMPLEMENTATIONS.clear()


# --- when things are due ---------------------------------------------------

def test_a_time_of_day_means_the_owners_time_of_day():
    """"Seven in the morning" is a wall clock, not a UTC offset.

    Computed in the owner's zone so daylight saving and timezone maths
    are not something they have to think about.
    """
    after = datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc)   # 05:30 IST
    due = scheduler.next_due(7, 0, [], "Asia/Kolkata", after=after)

    assert due.tzinfo is timezone.utc
    local = due.astimezone(scheduler._zone("Asia/Kolkata"))
    assert (local.hour, local.minute) == (7, 0)
    assert due == datetime(2026, 9, 8, 1, 30, tzinfo=timezone.utc)


def test_a_time_already_past_today_lands_tomorrow():
    after = datetime(2026, 9, 8, 6, 0, tzinfo=timezone.utc)   # 11:30 IST
    due = scheduler.next_due(7, 0, [], "Asia/Kolkata", after=after)
    assert due.date() == datetime(2026, 9, 9).date()


def test_chosen_days_are_honoured():
    """Weekdays only should not fire on Saturday."""
    friday = datetime(2026, 9, 11, 6, 0, tzinfo=timezone.utc)
    due = scheduler.next_due(7, 0, [1, 2, 3, 4, 5], "Asia/Kolkata", after=friday)
    local = due.astimezone(scheduler._zone("Asia/Kolkata"))
    assert local.isoweekday() == 1, "it should wait for Monday, not run at the weekend"


def test_an_unknown_timezone_does_not_stop_the_clock():
    """A typo in a setting must not mean nothing ever runs again."""
    due = scheduler.next_due(7, 0, [], "Mars/Olympus")
    assert due.tzinfo is timezone.utc


# --- the money guards ------------------------------------------------------

@pytest.mark.asyncio
async def test_unattended_work_stops_well_short_of_the_ceiling(clean, monkeypatch):
    """It must never be what exhausts the budget.

    If a schedule can spend the last rupee of the month, the owner finds
    out by being unable to talk to their own assistant.
    """
    await builtin.install()
    await scheduler.create("look something up", hour=7)
    await clean.execute("UPDATE schedules SET next_due_at = now() - interval '1 minute'")

    # 60% of 3500 is 2100; this month is past it.
    monkeypatch.setattr("app.budget.get_month_spend_inr",
                        _returns(Decimal("2200")))
    ran = []
    monkeypatch.setattr("app.agents.orchestrator.run", _records(ran))

    assert await scheduler.run_due(SETTINGS) == []
    assert not ran, "scheduled work spent money past its share of the budget"

    row = await clean.fetchrow("SELECT last_outcome FROM schedules")
    assert "paused" in row["last_outcome"], (
        "it stopped silently; the owner would never learn why nothing ran"
    )


@pytest.mark.asyncio
async def test_below_its_share_it_runs(clean, monkeypatch):
    await builtin.install()
    await scheduler.create("look something up", hour=7)
    await clean.execute("UPDATE schedules SET next_due_at = now() - interval '1 minute'")

    monkeypatch.setattr("app.budget.get_month_spend_inr", _returns(Decimal("100")))
    ran = []
    monkeypatch.setattr("app.agents.orchestrator.run", _records(ran))

    out = await scheduler.run_due(SETTINGS)
    assert len(out) == 1 and ran == ["look something up"]


@pytest.mark.asyncio
async def test_a_schedule_cannot_run_away(clean, monkeypatch):
    """A daily cap, because a loop is a bill nobody notices until month end."""
    await builtin.install()
    await scheduler.create("look something up", hour=7, max_per_day=2)
    monkeypatch.setattr("app.budget.get_month_spend_inr", _returns(Decimal("0")))
    ran = []
    monkeypatch.setattr("app.agents.orchestrator.run", _records(ran))

    for _ in range(5):
        await clean.execute(
            "UPDATE schedules SET next_due_at = now() - interval '1 minute'")
        await scheduler.run_due(SETTINGS)

    assert len(ran) == 2, f"the daily cap was ignored; it ran {len(ran)} times"


@pytest.mark.asyncio
async def test_being_late_means_running_once_not_catching_up(clean, monkeypatch):
    """A free-tier server sleeps, so a 7am job may be seen at 9am.

    Running it late is almost always what was wanted. Running it four
    times because four slots passed is not.
    """
    await builtin.install()
    await scheduler.create("look something up", hour=7, max_per_day=5)
    await clean.execute(
        "UPDATE schedules SET next_due_at = now() - interval '3 days'")
    monkeypatch.setattr("app.budget.get_month_spend_inr", _returns(Decimal("0")))
    ran = []
    monkeypatch.setattr("app.agents.orchestrator.run", _records(ran))

    out = await scheduler.run_due(SETTINGS)

    assert len(ran) == 1
    assert out[0]["late_minutes"] > 60, (
        "how late it is must be recorded, or a stale result reads as fresh"
    )
    nxt = await clean.fetchval("SELECT next_due_at FROM schedules")
    assert nxt > datetime.now(timezone.utc), "it stayed due and would run again"


@pytest.mark.asyncio
async def test_two_runners_cannot_both_take_the_same_job(clean, monkeypatch):
    """Render starts the replacement before stopping the old one."""
    import asyncio

    await builtin.install()
    await scheduler.create("look something up", hour=7, max_per_day=9)
    await clean.execute("UPDATE schedules SET next_due_at = now() - interval '1 minute'")
    monkeypatch.setattr("app.budget.get_month_spend_inr", _returns(Decimal("0")))
    ran = []
    monkeypatch.setattr("app.agents.orchestrator.run", _records(ran, delay=0.05))

    await asyncio.gather(scheduler.run_due(SETTINGS), scheduler.run_due(SETTINGS))
    assert len(ran) == 1, f"the same job ran {len(ran)} times"


@pytest.mark.asyncio
async def test_switched_off_means_nothing_runs(clean, monkeypatch):
    await builtin.install()
    await scheduler.create("look something up", hour=7)
    await clean.execute("UPDATE schedules SET next_due_at = now() - interval '1 minute'")
    ran = []
    monkeypatch.setattr("app.agents.orchestrator.run", _records(ran))

    assert await scheduler.run_due(Settings(scheduler_enabled=False)) == []
    assert not ran


@pytest.mark.asyncio
async def test_a_disabled_schedule_is_left_alone(clean, monkeypatch):
    await builtin.install()
    made = await scheduler.create("look something up", hour=7)
    await scheduler.set_enabled(made["id"], False)
    await clean.execute("UPDATE schedules SET next_due_at = now() - interval '1 minute'")
    monkeypatch.setattr("app.budget.get_month_spend_inr", _returns(Decimal("0")))
    ran = []
    monkeypatch.setattr("app.agents.orchestrator.run", _records(ran))

    assert await scheduler.run_due(SETTINGS) == []
    assert not ran


@pytest.mark.asyncio
async def test_work_that_fails_is_recorded_and_the_clock_moves_on(clean, monkeypatch):
    """One broken job must not wedge the schedule for ever."""
    await builtin.install()
    await scheduler.create("look something up", hour=7)
    await clean.execute("UPDATE schedules SET next_due_at = now() - interval '1 minute'")
    monkeypatch.setattr("app.budget.get_month_spend_inr", _returns(Decimal("0")))

    async def boom(*a, **k):
        raise RuntimeError("the model fell over")

    monkeypatch.setattr("app.agents.orchestrator.run", boom)

    out = await scheduler.run_due(SETTINGS)
    assert out and out[0]["status"] == "failed"

    row = await clean.fetchrow("SELECT last_outcome, next_due_at FROM schedules")
    assert "failed" in row["last_outcome"]
    assert row["next_due_at"] > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_a_broken_tick_is_never_an_outage(clean, monkeypatch):
    """A scheduler that can take the process down turns a missed job into
    a dead JARVIS."""
    monkeypatch.setattr("app.db.get_pool", _raises(RuntimeError("no pool")))
    assert await scheduler.run_due(SETTINGS) == []


# --- helpers ---------------------------------------------------------------

def _returns(value):
    async def _fn(*a, **k):
        return value
    return _fn


def _raises(exc):
    def _fn(*a, **k):
        raise exc
    return _fn


def _records(sink, delay=0.0):
    async def _fn(objective, requested_by, *a, **k):
        import asyncio

        if delay:
            await asyncio.sleep(delay)
        sink.append(objective)
        return {"workflow_id": None, "status": "completed", "outputs": {}}
    return _fn


# --- the way in from outside -----------------------------------------------

@pytest_asyncio.fixture
async def api(clean):
    """The real routes, driven in this test's own event loop."""
    from types import SimpleNamespace

    import httpx
    from fastapi import FastAPI

    from app.auth import get_current_user
    from app.config import get_settings
    from app.routes import agents as agents_route

    app = FastAPI()
    app.include_router(agents_route.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        uid="owner", email="owner@example.com")

    def as_settings(settings):
        app.dependency_overrides[get_settings] = lambda: settings

    as_settings(SETTINGS)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield SimpleNamespace(client=client, settings=as_settings, app=app)


@pytest.mark.asyncio
async def test_a_schedule_can_be_made_listed_paused_and_removed(api):
    made = (await api.client.post("/v1/schedules", json={
        "objective": "check the EV subsidy", "hour": 7, "days": [1, 2, 3, 4, 5],
    })).json()
    assert made["objective"] == "check the EV subsidy"

    listed = (await api.client.get("/v1/schedules")).json()
    assert len(listed) == 1 and listed[0]["enabled"] is True

    await api.client.post(f"/v1/schedules/{made['id']}/enabled?enabled=false")
    assert (await api.client.get("/v1/schedules")).json()[0]["enabled"] is False

    assert (await api.client.delete(f"/v1/schedules/{made['id']}")).status_code == 200
    assert (await api.client.get("/v1/schedules")).json() == []


@pytest.mark.asyncio
async def test_a_nonsense_time_is_refused(api):
    for bad in ({"objective": "x", "hour": 25},
                {"objective": "x", "hour": 7, "minute": 99},
                {"objective": "  ", "hour": 7},
                {"objective": "x", "hour": 7, "days": [0]}):
        res = await api.client.post("/v1/schedules", json=bad)
        assert res.status_code in (400, 422), f"{bad} was accepted"


@pytest.mark.asyncio
async def test_the_wake_up_endpoint_does_not_exist_without_a_key(api):
    """An open trigger for work that spends money is not a default."""
    api.settings(Settings(cron_key="", scheduler_enabled=True))
    res = await api.client.post("/v1/cron/tick")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_the_wake_up_endpoint_refuses_a_wrong_key(api):
    api.settings(Settings(cron_key="the-real-key", scheduler_enabled=True))
    assert (await api.client.post("/v1/cron/tick")).status_code == 403
    assert (await api.client.post(
        "/v1/cron/tick", headers={"x-cron-key": "guess"})).status_code == 403


@pytest.mark.asyncio
async def test_the_right_key_runs_what_is_due(api, monkeypatch):
    await builtin.install()
    api.settings(Settings(cron_key="the-real-key", scheduler_enabled=True,
                          monthly_budget_inr=Decimal("3500"),
                          scheduler_budget_percent=60))
    await scheduler.create("look something up", hour=7)
    from app.db import execute

    await execute("UPDATE schedules SET next_due_at = now() - interval '1 minute'")
    monkeypatch.setattr("app.budget.get_month_spend_inr", _returns(Decimal("0")))
    ran = []
    monkeypatch.setattr("app.agents.orchestrator.run", _records(ran))

    body = (await api.client.post(
        "/v1/cron/tick", headers={"x-cron-key": "the-real-key"})).json()

    assert body["ran"] == 1
    assert ran == ["look something up"]
