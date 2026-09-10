"""What is happening right now, as one answer.

This module exists because the same question kept being unanswerable. The
owner asked for a script, was told the Media Director was working on it,
and there was no script -- and neither he nor I could tell whether nothing
had ever started or something had started and stalled. Every fix before
this one was a guess at which.

So the tests care about that distinction above all: "nothing is running"
and "nothing has ever been started" are different answers to "where is my
script", and only the second says the request never got through.
"""
import pytest
import pytest_asyncio

from app import activity
from app.agents import registry, tasks


@pytest_asyncio.fixture
async def clean(db_pool):
    await db_pool.execute(
        "DELETE FROM content_pieces; DELETE FROM tasks; "
        "DELETE FROM workflows; DELETE FROM agents;"
    )
    registry._IMPLEMENTATIONS.clear()
    yield db_pool
    registry._IMPLEMENTATIONS.clear()


async def a_running_task(pool, capability="research.web", minutes_ago=0):
    workflow = await tasks.create_workflow("Find it out", "user:owner")
    task_id = await tasks.create(objective="Find it out",
                                 capability=capability, workflow_id=workflow)
    await pool.execute(
        f"UPDATE tasks SET status = 'running', "
        f"started_at = now() - interval '{int(minutes_ago)} minutes' "
        f"WHERE id = $1", task_id)
    await pool.execute(
        "UPDATE workflows SET status = 'running' WHERE id = $1", workflow)
    return workflow, task_id


# --- the distinction that was missing -------------------------------------

@pytest.mark.asyncio
async def test_nothing_ever_started_is_a_different_answer_from_nothing_running(clean):
    """"Where is my script" has two very different true answers."""
    never = await activity.snapshot()
    assert never["busy"] is False
    assert "has ever been started" in never["said"]

    workflow = await clean.fetchval(
        "INSERT INTO workflows (objective, requested_by, status) "
        "VALUES ('Media: x','user:owner','completed') RETURNING id")
    await clean.execute(
        "INSERT INTO content_pieces (workflow_id, topic, brand, state) "
        "VALUES ($1,'a topic','ai_media','ready')", workflow)

    since = await activity.snapshot()
    assert since["busy"] is False
    assert "has ever been started" not in since["said"]
    assert "Nothing is running" in since["said"]


@pytest.mark.asyncio
async def test_a_running_step_is_named_with_how_long_it_has_been_going(clean):
    await a_running_task(clean, minutes_ago=2)

    live = await activity.snapshot()
    assert live["busy"] is True
    assert len(live["running"]) == 1
    assert live["running"][0]["capability"] == "research.web"
    assert live["running"][0]["minutes"] == 2
    assert "research.web" in live["said"]


@pytest.mark.asyncio
async def test_long_enough_is_called_stuck_rather_than_working(clean):
    """The difference between slow and stuck is the whole point."""
    await a_running_task(clean, minutes_ago=1)
    assert (await activity.snapshot())["stalled"] is False

    await a_running_task(clean, minutes_ago=40)
    live = await activity.snapshot()
    assert live["stalled"] is True
    assert "stuck rather than slow" in live["said"]


@pytest.mark.asyncio
async def test_a_piece_reports_how_far_through_it_is(clean):
    workflow, _ = await a_running_task(clean, "media.script", minutes_ago=1)
    await tasks.create(objective="research", capability="research.web",
                       workflow_id=workflow)
    await clean.execute(
        "UPDATE tasks SET status = 'completed' WHERE capability = 'research.web'")
    await clean.execute(
        "INSERT INTO content_pieces (workflow_id, topic, brand, state) "
        "VALUES ($1,'the benchmark','ai_media','producing')", workflow)

    live = await activity.snapshot()
    assert len(live["pieces"]) == 1
    piece = live["pieces"][0]
    assert piece["topic"] == "the benchmark"
    assert piece["done"] == 1 and piece["steps"] == 2
    assert "1/2 steps" in live["said"]


@pytest.mark.asyncio
async def test_work_belonging_to_a_finished_workflow_is_not_reported_as_live(clean):
    """A failed workflow leaves its dependents blocked for ever. Counting
    those is how an idle system looks busy."""
    workflow, _ = await a_running_task(clean)
    await tasks.create(objective="later", capability="factcheck.claims",
                       workflow_id=workflow)
    await clean.execute(
        "UPDATE workflows SET status = 'failed' WHERE id = $1", workflow)

    live = await activity.snapshot()
    assert live["busy"] is False
    assert live["running"] == [] and live["queued"] == []


@pytest.mark.asyncio
async def test_the_words_and_the_rows_cannot_disagree(clean):
    """The sentence and the panel come from one place on purpose: they
    disagreeing is exactly what went wrong."""
    empty = await activity.snapshot()
    assert empty["said"] and not empty["running"] and not empty["pieces"]

    await a_running_task(clean, minutes_ago=1)
    live = await activity.snapshot()
    assert str(len(live["running"])) in live["said"]
