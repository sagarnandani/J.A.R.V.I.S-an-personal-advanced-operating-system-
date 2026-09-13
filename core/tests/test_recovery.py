"""Knowing which version worked, and being honest about what that means.

The strongest property here is not in this file: JARVIS cannot merge,
push, rebase, reset or check out, so no change it writes can reach the
running code at all. These tests cover the weaker, still necessary part --
that there is an identifiable last-known-good to go back to when the
owner merges something himself and it turns out to be wrong.
"""
import subprocess
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from app.dev import recovery


class Settings:
    def __init__(self, path):
        self.repo_path = str(path)


def a_repo() -> Path:
    tmp = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp)], check=True)
    (tmp / "a.txt").write_text("one\n")
    subprocess.run(["git", "-C", str(tmp), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp), "-c", "user.name=t",
                    "-c", "user.email=t@t", "commit", "-qm", "one"], check=True)
    return tmp


def commit_again(where: Path, text: str) -> str:
    (where / "a.txt").write_text(text)
    subprocess.run(["git", "-C", str(where), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(where), "-c", "user.name=t",
                    "-c", "user.email=t@t", "commit", "-qm", text], check=True)
    return subprocess.run(["git", "-C", str(where), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


@pytest_asyncio.fixture
async def clean(db_pool):
    await db_pool.execute("DELETE FROM known_good")
    yield db_pool
    await db_pool.execute("DELETE FROM known_good")


@pytest.mark.asyncio
async def test_starting_up_records_the_commit_that_is_running(clean):
    where = a_repo()
    noted = await recovery.record_running_version(Settings(where))
    assert noted
    head = subprocess.run(["git", "-C", str(where), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    assert noted["commit_sha"] == head
    assert noted["branch"] == "main"


@pytest.mark.asyncio
async def test_restarting_the_same_version_does_not_fill_the_table(clean):
    """Otherwise a container that restarts every few minutes buries the
    history that makes this useful."""
    where = a_repo()
    settings = Settings(where)
    for _ in range(4):
        await recovery.record_running_version(settings)
    assert len(await recovery.history(20)) == 1


@pytest.mark.asyncio
async def test_going_back_never_offers_the_version_that_is_running(clean):
    """The single most useless answer available at the moment it is
    asked for is the commit you are already on and trying to escape."""
    where = a_repo()
    settings = Settings(where)

    await recovery.record_running_version(settings)
    first = (await recovery.latest())["commit_sha"]

    second = commit_again(where, "two\n")
    await recovery.record_running_version(settings)

    back = await recovery.previous_to_running(settings)
    assert back["commit_sha"] == first
    assert back["commit_sha"] != second


@pytest.mark.asyncio
async def test_it_hands_over_a_command_rather_than_a_description(clean):
    """At the point this is read, something is broken and nobody wants to
    assemble a git invocation out of a paragraph."""
    where = a_repo()
    settings = Settings(where)
    await recovery.record_running_version(settings)
    commit_again(where, "two\n")
    await recovery.record_running_version(settings)

    state = await recovery.state(settings)
    assert state["command"] and state["command"].startswith("git checkout ")
    assert state["can_go_back_to"]


@pytest.mark.asyncio
async def test_with_nothing_recorded_it_says_so_rather_than_offering_nothing(
        clean):
    where = a_repo()
    state = await recovery.state(Settings(where))
    assert state["command"] is None
    assert "nothing to go back to" in state["why_not"]


@pytest.mark.asyncio
async def test_it_says_plainly_what_known_good_does_not_mean(clean):
    """The name claims more than the check does. Saying so is the
    difference between a safety property and a comforting label."""
    state = await recovery.state(Settings(a_repo()))
    assert "does not mean the release was correct" in state["honestly"]


@pytest.mark.asyncio
async def test_a_directory_with_no_git_history_records_nothing(clean):
    """A container built from a copy of the source has no history. That
    is normal -- it must not be an error, and it must not invent a
    commit."""
    assert await recovery.record_running_version(
        Settings(Path(tempfile.mkdtemp()))) is None
    assert await recovery.history() == []


@pytest.mark.asyncio
async def test_a_reverted_change_is_recorded_as_reverted(clean, db_pool):
    """A table that cannot tell 'approved' from 'approved and then undone'
    will happily report a success rate that never happened."""
    from app.dev import records

    await db_pool.execute("DELETE FROM change_requests")
    made = await records.open_request("A change", "a brief")
    await recovery.mark_reverted(made["id"], "sagar")

    row = await records.get(made["id"])
    assert row["reverted_at"] is not None
    assert "reverted by sagar" in row["reason"]
    await db_pool.execute("DELETE FROM change_requests")
