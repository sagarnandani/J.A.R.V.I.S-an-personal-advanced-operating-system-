"""Picking work back up after a restart.

Yesterday's version made the symptom quieter instead of fixing it. A task
interrupted mid-flight used to sit at `running` for ever; recovery moved
it to `queued`, where it sat for ever instead, because nothing in the
system advances a queued task on its own. The owner's experience was
identical: he asked for a script, was told it was in progress, and ten
minutes later there was still no script.

So these tests care about one thing above all: that after recovery the
work actually runs.
"""
import pytest
import pytest_asyncio

from app import resume
from app.agents import registry, tasks
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
        "DELETE FROM content_pieces; DELETE FROM agent_metrics; "
        "DELETE FROM agent_events; DELETE FROM tasks; "
        "DELETE FROM workflows; DELETE FROM agents;"
    )
    registry._IMPLEMENTATIONS.clear()
    yield db_pool
    registry._IMPLEMENTATIONS.clear()


async def a_capability(name="general.research", output="done"):
    await registry.register(AgentSpec(
        capability=name, name=name, task_types=("general",),
        permissions=frozenset({Permission.READ_MEMORY}),
        model_tiers=(ModelTier.STANDARD,), status=Lifecycle.ACTIVE,
    ))

    async def run(handoff, choice):
        return AgentResult(output=output, confidence=0.8,
                           tokens_in=10, tokens_out=5)

    registry.implement(name, run)


async def an_interrupted_workflow(pool, capability="general.research"):
    workflow = await tasks.create_workflow("Interrupted", "user:owner")
    task_id = await tasks.create(objective="Do it", capability=capability,
                                 workflow_id=workflow)
    await pool.execute(
        "UPDATE tasks SET status = 'running', attempts = 1, "
        "started_at = now() - interval '2 hours' WHERE id = $1", task_id)
    await pool.execute(
        "UPDATE workflows SET status = 'running', updated_at = now() "
        "WHERE id = $1", workflow)
    return workflow, task_id


# --- the half that was missing --------------------------------------------

@pytest.mark.asyncio
async def test_interrupted_work_is_not_only_requeued_it_is_run(clean):
    """The bug, as a test. Re-queueing alone left it queued for ever."""
    await a_capability()
    workflow, task_id = await an_interrupted_workflow(clean)

    summary = await resume.after_restart()
    assert summary["recovered"] == 1
    assert summary["resumed"], "nothing was picked back up"

    # The work is detached, so wait for it the way the process would --
    # and wait on the workflow, which settles after the task, rather than
    # on the task and then reading the workflow a moment too early.
    for _ in range(80):
        if (await tasks.get_workflow(workflow))["status"] == "completed":
            break
        await _tick()

    assert (await tasks.get(task_id))["status"] == "completed", (
        "the task was re-queued and then never ran"
    )
    assert (await tasks.get_workflow(workflow))["status"] == "completed", (
        "the task ran but the workflow never settled"
    )


async def _tick():
    import asyncio

    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_a_piece_goes_back_through_the_director_not_the_orchestrator(clean):
    """Advancing the graph would finish the steps and leave the content
    record at 'producing' for ever, because the gates and the outcome live
    in the Director's settle step."""
    from app.media import records

    for name in ("research.web", "factcheck.claims", "media.strategy",
                 "media.script", "media.review"):
        await a_capability(name, output={"summary": "x", "verdict": "pass",
                                         "publish": True, "brand": "ai_media",
                                         "title_options": ["A title"]})

    workflow, task_id = await an_interrupted_workflow(clean, "media.script")
    piece_id = await records.open_piece(workflow, "A topic", "ai_media")

    await resume.after_restart()

    for _ in range(60):
        piece = await records.get(piece_id)
        if piece["state"] != "producing":
            break
        await _tick()
    assert (await records.get(piece_id))["state"] != "producing", (
        "the piece was left being made for ever"
    )


# --- the bounds, because this spends money without being asked ------------

@pytest.mark.asyncio
async def test_work_too_old_is_closed_off_rather_than_started(clean):
    """A workflow from three days ago quietly spending on a restart is a
    worse surprise than one that says it was interrupted."""
    await a_capability()
    workflow, _ = await an_interrupted_workflow(clean)
    await clean.execute(
        "UPDATE workflows SET updated_at = now() - interval '3 days' "
        "WHERE id = $1", workflow)

    summary = await resume.after_restart()

    assert summary["abandoned"] == 1
    assert summary["resumed"] == []
    row = await tasks.get_workflow(workflow)
    assert row["status"] == "failed"
    assert "Interrupted by a restart" in row["failure_reason"]


@pytest.mark.asyncio
async def test_an_old_piece_is_marked_failed_not_left_being_made(clean):
    from app.media import records

    await a_capability("media.script")
    workflow, _ = await an_interrupted_workflow(clean, "media.script")
    piece_id = await records.open_piece(workflow, "A topic", "ai_media")
    await clean.execute(
        "UPDATE workflows SET updated_at = now() - interval '3 days' "
        "WHERE id = $1", workflow)

    await resume.after_restart()

    piece = await records.get(piece_id)
    assert piece["state"] == "failed"
    assert piece["reason"]


@pytest.mark.asyncio
async def test_only_a_few_are_restarted_at_once(clean):
    """A deploy after a busy afternoon should not start ten workflows in
    the same second on a free tier."""
    await a_capability()
    for _ in range(resume.MAX_RESUMED + 3):
        await an_interrupted_workflow(clean)

    await tasks.recover_stuck()
    picked = await resume.resume_all()
    assert len(picked) == resume.MAX_RESUMED


@pytest.mark.asyncio
async def test_a_finished_workflow_is_never_restarted(clean):
    await a_capability()
    workflow, task_id = await an_interrupted_workflow(clean)
    await tasks.recover_stuck()
    await clean.execute(
        "UPDATE workflows SET status = 'completed' WHERE id = $1", workflow)

    assert await resume.unfinished() == []


@pytest.mark.asyncio
async def test_nothing_to_recover_is_quiet(clean):
    summary = await resume.after_restart()
    assert summary == {"recovered": 0, "abandoned": 0, "resumed": []}
