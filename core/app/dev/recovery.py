"""The last version of JARVIS known to work, and how to get back to it.

Brief section 17: "Self-development must never be capable of destroying
the only working version of JARVIS."

That sentence is already structurally true here and it is worth saying
exactly why, because the reason is stronger than anything this file adds.
JARVIS cannot merge, push, rebase, reset or check out -- those verbs are
absent from app/dev/repo.py, not guarded in it. Every change it writes
lives on a branch nobody has merged. Undoing one is deleting a branch.
There is no sequence of self-development that reaches the running code.

So what is actually missing is the case *after* the owner merges
something, restarts, and it is broken. JARVIS cannot fix that -- it is
the thing that is broken -- and a design where it could would mean a
JARVIS that can check out arbitrary commits, which is a far larger hole
than the problem it solves.

What it can do, and does here:

  * **Remember.** Every time it starts up and works, it writes down the
    commit it is running. That list is the answer to "what was the last
    version that worked", which is the question you cannot answer at the
    moment you need it.
  * **Say so.** /health and the dashboard carry the commit to go back to
    and the command to do it, so recovery does not depend on having read
    the documentation beforehand.

**What "known good" actually means here, stated plainly.** It means the
process started, the database was reachable, the schema was current and
the agents installed. It does not mean the release was correct. A version
with a broken reply path would still record itself. This is a
significantly weaker claim than the name suggests, and naming it
`known_good` and then not saying so would be the dishonest version.
"""
import logging

from app.db import execute, fetch, fetchrow
from app.dev import repo

logger = logging.getLogger("jarvis.dev.recovery")

# How many to keep. Enough to skip back past a bad run or two; not so
# many that the table becomes a second git log.
KEEP = 50


async def record_running_version(settings=None) -> dict | None:
    """Write down the commit that just started successfully.

    Called once from startup, after everything that has to work has
    worked. Never called from the self-development path -- a change
    approving itself as known-good would make the record worthless.
    """
    try:
        state = await repo.state(settings)
    except Exception as exc:  # noqa: BLE001 - never fail startup over this
        logger.info("Could not read the running version: %s", exc)
        return None

    commit = state.get("head")
    if not commit:
        # A container built from a copy of the source has no git history.
        # That is normal and not an error; there is simply nothing to
        # record, and /health will say so rather than inventing a commit.
        return None

    previous = await latest()
    if previous and previous["commit_sha"] == commit:
        return previous

    row = await fetchrow(
        "INSERT INTO known_good (commit_sha, branch, healthy, detail) "
        "VALUES ($1, $2, TRUE, $3) RETURNING *",
        commit, state.get("branch"),
        {"note": "started, database reachable, schema current, agents installed"},
    )
    await execute(
        "DELETE FROM known_good WHERE id NOT IN "
        "(SELECT id FROM known_good ORDER BY noted_at DESC LIMIT $1)",
        KEEP,
    )
    logger.info("Recorded a working version: %s on %s", commit[:12],
                state.get("branch"))
    return dict(row) if row else None


async def latest() -> dict | None:
    row = await fetchrow(
        "SELECT * FROM known_good WHERE healthy ORDER BY noted_at DESC LIMIT 1"
    )
    return dict(row) if row else None


async def history(limit: int = 10) -> list[dict]:
    rows = await fetch(
        "SELECT commit_sha, branch, noted_at, healthy FROM known_good "
        "ORDER BY noted_at DESC LIMIT $1",
        min(max(int(limit), 1), KEEP),
    )
    return [dict(r) for r in rows]


async def previous_to_running(settings=None) -> dict | None:
    """The last working version that is not the one running now.

    What you want when the version running now is the problem. Returning
    the current commit would be the single most useless answer available
    at the moment it is asked for.
    """
    try:
        running = (await repo.state(settings)).get("head")
    except Exception:  # noqa: BLE001
        running = None

    rows = await fetch(
        "SELECT * FROM known_good WHERE healthy ORDER BY noted_at DESC LIMIT 10"
    )
    for row in rows:
        if row["commit_sha"] != running:
            return dict(row)
    return None


async def state(settings=None) -> dict:
    """What recovery is possible, and the command that performs it.

    The command is spelled out rather than described. At the point this
    is read, something is broken and nobody wants to compose a git
    invocation from a paragraph of prose.
    """
    back_to = await previous_to_running(settings)
    current = await latest()

    info = {
        "running": (current or {}).get("commit_sha"),
        "can_go_back_to": (back_to or {}).get("commit_sha"),
        "noted_at": (back_to or {}).get("noted_at"),
        "history": await history(5),
        "means": (
            "Changes JARVIS writes live on branches that were never "
            "merged, so undoing one is deleting the branch. This is for "
            "the other case: a version you merged and started that turned "
            "out to be broken."
        ),
        "honestly": (
            "'Known good' here means it started, reached the database, "
            "applied its schema and installed its agents. It does not "
            "mean the release was correct."
        ),
    }
    if back_to:
        sha = back_to["commit_sha"]
        info["command"] = (
            f"git checkout {sha[:12]} && docker compose up -d --build"
        )
    else:
        info["command"] = None
        info["why_not"] = (
            "No earlier working version has been recorded yet. This "
            "fills up as JARVIS restarts -- there is nothing to go back "
            "to on a first run."
        )
    return info


async def mark_reverted(request_id, by: str) -> bool:
    """Note that a change was undone, whoever undid it.

    Recorded so the history reads truthfully. A change that was approved
    and then reverted is a different story from one that was approved,
    and a table that cannot tell them apart will happily report a success
    rate that never happened.
    """
    from datetime import datetime, timezone

    from app.dev import records

    row = await records.get(request_id)
    if row is None:
        return False
    await records.update(request_id, reverted_at=datetime.now(timezone.utc),
                         reason=f"{row.get('reason') or ''} "
                                f"[reverted by {by}]".strip())
    logger.warning("Change %s was marked reverted by %s", request_id, by)
    return True
