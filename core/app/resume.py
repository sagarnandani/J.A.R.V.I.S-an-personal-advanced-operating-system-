"""Picking work back up after a restart, properly this time.

Yesterday's fix was half a fix and it made the symptom quieter rather
than fixing it. A task interrupted mid-flight used to sit at `running`
for ever; recovery moved it to `queued`, where it sat for ever instead.
Nothing in the system advances a queued task on its own: the orchestrator
runs a workflow's graph when somebody asks it to, and after a restart
nobody is asking.

So recovery has two halves and only one of them existed. Re-queue the
task, then actually run the workflow it belongs to.

Media workflows need the Director rather than the orchestrator. Advancing
the graph would run the remaining steps and settle the workflow, but the
content record would stay at `producing` for ever, because the gates and
the outcome live in the Director's settle step. Going through it is what
makes the piece end up in a state the owner can act on.

Two bounds, because this spends real money without being asked. Only
recent work is resumed, and only a few pieces at once. Anything older is
marked failed with a reason -- a workflow from three days ago quietly
starting up and spending on a restart is a worse surprise than one that
says it was interrupted.
"""
import asyncio
import logging

from app.db import execute, fetch

logger = logging.getLogger("jarvis.resume")

# How recent a workflow has to be to be picked back up automatically.
RESUMABLE_HOURS = 6
# How many to restart at once. A deploy after a busy afternoon should not
# start ten workflows in the same second on a free tier.
MAX_RESUMED = 5

_RUNNING: set[asyncio.Task] = set()


async def unfinished(hours: int = RESUMABLE_HOURS) -> list[dict]:
    """Workflows with work left to do and nothing running them."""
    rows = await fetch(
        """
        SELECT w.id, w.objective, w.updated_at,
               c.id AS piece_id, c.topic, c.brand
          FROM workflows w
          LEFT JOIN content_pieces c ON c.workflow_id = w.id
         WHERE w.status NOT IN ('completed', 'failed', 'cancelled')
           AND w.updated_at > now() - ($1 || ' hours')::interval
           AND EXISTS (
               SELECT 1 FROM tasks t
                WHERE t.workflow_id = w.id
                  AND t.status IN ('queued', 'blocked')
           )
         ORDER BY w.updated_at DESC
        """,
        str(int(hours)),
    )
    return [dict(r) for r in rows]


async def abandon_old(hours: int = RESUMABLE_HOURS) -> int:
    """Close off work too old to restart without asking.

    Marked failed with a reason rather than left queued. A workflow that
    sits queued for ever is indistinguishable from one that is about to
    run, and the owner cannot tell which they are looking at.
    """
    result = await execute(
        """
        UPDATE workflows
           SET status = 'failed',
               failure_reason = COALESCE(failure_reason,
                   'Interrupted by a restart and left too long to pick back '
                   'up on its own. Start it again if it is still wanted.'),
               finished_at = now(), updated_at = now()
         WHERE status NOT IN ('completed', 'failed', 'cancelled')
           AND updated_at <= now() - ($1 || ' hours')::interval
        """,
        str(int(hours)),
    )
    closed = int(result.rsplit(" ", 1)[-1] or 0)
    if closed:
        await execute(
            """
            UPDATE content_pieces
               SET state = 'failed',
                   reason = COALESCE(reason, 'Interrupted by a restart.'),
                   updated_at = now()
             WHERE state = 'producing'
               AND workflow_id IN (SELECT id FROM workflows WHERE status = 'failed')
            """
        )
    return closed


def _detach(coro, what: str) -> None:
    task = asyncio.create_task(coro)
    _RUNNING.add(task)

    def _done(finished: asyncio.Task) -> None:
        _RUNNING.discard(finished)
        if not finished.cancelled() and finished.exception():
            logger.error("Resumed %s ended in an exception: %s",
                         what, finished.exception())

    task.add_done_callback(_done)


async def resume_all() -> list[str]:
    """Restart what was interrupted. Returns what was picked up."""
    from app.agents import orchestrator
    from app.media import brands, director

    picked: list[str] = []
    for row in (await unfinished())[:MAX_RESUMED]:
        workflow_id = row["id"]
        if row["piece_id"]:
            # Through the Director, so the gates run and the piece ends up
            # in a state the owner can act on.
            _detach(
                director.run(workflow_id, row["topic"] or row["objective"],
                             row["brand"] or brands.AI_MEDIA),
                f"piece {row['piece_id']}",
            )
            picked.append(f"media: {row['topic'] or row['objective']}")
        else:
            _detach(orchestrator.advance(workflow_id), f"workflow {workflow_id}")
            picked.append(str(row["objective"]))
    return picked


async def after_restart() -> dict:
    """The whole recovery, in the order it has to happen.

    Re-queue first: a task still marked running would not be counted as
    work left to do, so resuming before recovering would find nothing and
    report success.
    """
    from app.agents import tasks

    summary = {"recovered": 0, "abandoned": 0, "resumed": []}
    try:
        summary["recovered"] = len(await tasks.recover_stuck())
        summary["abandoned"] = await abandon_old()
        summary["resumed"] = await resume_all()
    except Exception as exc:  # noqa: BLE001 - never worth refusing to start
        logger.warning("Could not pick up interrupted work: %s", exc)
    return summary
