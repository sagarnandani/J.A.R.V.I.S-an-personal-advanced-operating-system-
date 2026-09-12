"""JARVIS changing its own code, without being able to break itself doing it.

The dangerous version of self-modification is a process editing the files
it is running from. These tests exist to prove this is the other version.

Most of them are about what CANNOT happen: writing outside the worktree,
building on a protected branch, building from a dirty checkout, moving
the running checkout's HEAD, merging, pushing. Those are the properties
that make the rest safe to have at all, so they are tested against a real
git repository rather than a mock -- a mocked `git` would agree with
whatever I believed while writing it.
"""
import subprocess
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from app.dev import patch as dev_patch
from app.dev import plan as dev_plan
from app.dev import repo


class Settings:
    def __init__(self, path):
        self.repo_path = str(path)


def a_repo(dirty: bool = False) -> Path:
    """A real repository, thrown away afterwards."""
    tmp = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp)], check=True)
    (tmp / "core").mkdir()
    (tmp / "core" / "thing.py").write_text("VALUE = 1\n")
    (tmp / "README.md").write_text("# A project\n")
    subprocess.run(["git", "-C", str(tmp), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp), "-c", "user.name=t",
                    "-c", "user.email=t@t", "commit", "-qm", "first"], check=True)
    if dirty:
        (tmp / "core" / "thing.py").write_text("VALUE = 99\n")
    return tmp


@pytest_asyncio.fixture
async def worktree():
    where = a_repo()
    settings = Settings(where)
    made = await repo.open_worktree("add a thing", settings)
    yield where, settings, made
    await repo.close_worktree(made["path"], settings)


# --- what cannot happen ----------------------------------------------------

@pytest.mark.asyncio
async def test_writing_in_the_worktree_does_not_touch_the_running_checkout(worktree):
    """The whole point. A process editing the files it runs from is the
    version of this that ends badly."""
    where, settings, made = worktree

    await repo.write_file(made["path"], "core/thing.py", "VALUE = 2\n")

    assert (where / "core" / "thing.py").read_text() == "VALUE = 1\n"
    assert (Path(made["path"]) / "core" / "thing.py").read_text() == "VALUE = 2\n"


@pytest.mark.asyncio
async def test_the_running_checkout_stays_on_its_own_branch(worktree):
    where, settings, made = worktree
    await repo.write_file(made["path"], "core/thing.py", "VALUE = 2\n")
    await repo.commit(made["path"], "Change it", settings)

    on = subprocess.run(["git", "-C", str(where), "rev-parse", "--abbrev-ref", "HEAD"],
                        capture_output=True, text=True).stdout.strip()
    assert on == "main", "the running checkout's branch was moved"
    assert (where / "core" / "thing.py").read_text() == "VALUE = 1\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("escape", [
    "../escaped.txt", "../../etc/passwd", "core/../../outside.py",
    ".git/config", "core/../.git/hooks/pre-commit",
])
async def test_it_refuses_to_write_outside_the_worktree(worktree, escape):
    """A plan naming `../../.ssh/authorized_keys` is refused rather than
    followed. Resolved first, because `a/../../b` is only obviously
    outside once it has been."""
    _, _, made = worktree
    with pytest.raises(repo.RepoError):
        repo.inside(made["path"], escape)


@pytest.mark.asyncio
async def test_it_will_not_build_from_a_dirty_checkout():
    """Otherwise the owner's own unfinished edits end up in a branch he
    did not write, and the diff blames JARVIS for them."""
    where = a_repo(dirty=True)
    settings = Settings(where)

    state = await repo.state(settings)
    assert state["usable"] is False
    assert "uncommitted" in state["why"].lower()

    with pytest.raises(repo.RepoError):
        await repo.open_worktree("anything", settings)


@pytest.mark.asyncio
async def test_it_says_plainly_when_there_is_no_repository():
    """A brief that becomes a plan and then discovers there is no git
    repository has spent money and attention for nothing."""
    state = await repo.state(Settings(tempfile.mkdtemp()))
    assert state["usable"] is False
    assert "not a git repository" in state["why"]


@pytest.mark.parametrize("title,expected", [
    ("Add a Telegram bridge", "jarvis/add-a-telegram-bridge"),
    ("", "jarvis/change"),
    ("!!! ???", "jarvis/change"),
])
def test_a_branch_is_named_after_the_change(title, expected):
    assert repo.branch_name(title) == expected


def test_every_branch_is_namespaced_away_from_the_protected_ones():
    """A brief cannot talk JARVIS into building on main."""
    for title in ("main", "master", "production", "release"):
        made = repo.branch_name(title)
        assert made.startswith("jarvis/")
        assert made not in repo.PROTECTED


def test_nothing_here_can_push_or_merge():
    """Absent rather than guarded. A guard can be edited around; a
    function that does not exist cannot be called."""
    source = (Path(repo.__file__)).read_text()
    for forbidden in ('"push"', "'push'", '"merge"', "'merge'",
                      '"rebase"', '"reset"', '"checkout"'):
        assert forbidden not in source, f"repo.py can {forbidden}"


# --- what does happen ------------------------------------------------------

@pytest.mark.asyncio
async def test_a_change_becomes_a_branch_with_a_diff(worktree):
    where, settings, made = worktree
    await repo.write_file(made["path"], "core/thing.py", "VALUE = 2\n")
    await repo.write_file(made["path"], "core/new.py", "NEW = True\n")

    out = await repo.commit(made["path"], "Change the value", settings)

    assert out["committed"] is True
    assert sorted(out["files"]) == ["core/new.py", "core/thing.py"]
    assert "VALUE = 2" in out["diff"] and "VALUE = 1" in out["diff"]


@pytest.mark.asyncio
async def test_committing_nothing_says_so_rather_than_making_an_empty_branch(worktree):
    _, settings, made = worktree
    out = await repo.commit(made["path"], "Nothing", settings)
    assert out["committed"] is False
    assert "Nothing was changed" in out["why"]


@pytest.mark.asyncio
async def test_the_branch_survives_the_worktree_being_cleaned_up():
    """The branch is the deliverable. Removing it because a temporary
    directory was tidied would throw away the thing to be reviewed."""
    where = a_repo()
    settings = Settings(where)
    made = await repo.open_worktree("keep me", settings)
    await repo.write_file(made["path"], "core/thing.py", "VALUE = 3\n")
    await repo.commit(made["path"], "Keep me", settings)

    await repo.close_worktree(made["path"], settings)

    branches = subprocess.run(["git", "-C", str(where), "branch", "--list"],
                              capture_output=True, text=True).stdout
    assert "jarvis/keep-me" in branches
    assert not Path(made["path"]).exists()


@pytest.mark.asyncio
async def test_tests_that_fail_are_reported_as_failing(worktree):
    """A proposal with no test result is not a proposal, and one whose
    tests fail must never be described as working."""
    _, _, made = worktree
    out = await repo.run_tests(made["path"], ["python", "-c", "raise SystemExit(1)"])
    assert out["passed"] is False

    good = await repo.run_tests(made["path"], ["python", "-c", "pass"])
    assert good["passed"] is True


@pytest.mark.asyncio
async def test_a_test_command_that_does_not_exist_says_so(worktree):
    _, _, made = worktree
    out = await repo.run_tests(made["path"], ["definitely-not-a-command"])
    assert out["passed"] is None
    assert "could not run" in out["output"].lower()


# --- the agents ------------------------------------------------------------

def test_neither_agent_can_be_reached_by_the_planner():
    """They are steps of a supervised chain. A plan that could name
    dev.patch on its own would be a plan that writes code with no plan
    behind it."""
    for spec in (dev_plan.SPEC, dev_patch.SPEC):
        assert spec.supervisor == "dev.director"


def test_the_writer_cannot_publish_or_change_what_agents_exist():
    from app.agents.schemas import NEVER_DELEGATED, Permission

    held = dev_plan.SPEC.permissions | dev_patch.SPEC.permissions
    assert Permission.PUBLISH not in held
    assert not (held & NEVER_DELEGATED), (
        "a code-writing agent holds a permission no agent may be delegated"
    )


def test_the_planner_may_read_but_not_write():
    from app.agents.schemas import Permission

    assert Permission.READ_FILES in dev_plan.SPEC.permissions
    assert Permission.WRITE_FILES not in dev_plan.SPEC.permissions, (
        "the planner can write, which makes the plan-then-build gate pointless"
    )


def test_a_change_may_not_sprawl_across_the_codebase():
    """A change spanning twenty files is one nobody reviews properly, and
    an unreviewed change is what this whole path exists to prevent."""
    assert dev_plan.MAX_FILES <= 8
    assert str(dev_plan.MAX_FILES) in dev_plan._PROMPT or "{limit}" in dev_plan._PROMPT


# --- the truncation guard --------------------------------------------------

@pytest.mark.asyncio
async def test_a_rewrite_that_came_back_truncated_is_not_written(monkeypatch):
    """Truncated Python does not parse, and a file cut off half way is
    almost always a model running out of output tokens rather than a
    deliberate deletion. Caught here rather than left to the reviewer,
    because it fails the tests with a far more confusing message."""
    import json
    from types import SimpleNamespace

    import app.llm
    from app.agents.schemas import Handoff

    long_file = "\n".join(f"LINE_{i} = {i}" for i in range(400))

    class Fake:
        async def complete(self, message, history=None, memory_context=None):
            return SimpleNamespace(
                text=json.dumps({"changed": True, "content": "LINE_0 = 0\n"}),
                input_tokens=10, output_tokens=5, model="t", provider="mock")

    monkeypatch.setattr(app.llm, "get_provider", lambda settings: Fake())

    result = await dev_patch.run(
        Handoff(task_id=None, workflow_id=None, objective="Write it",
                inputs={"path": "core/thing.py", "current": long_file},
                context="", constraints={}, permissions=frozenset()),
        None,
    )

    assert result.output["changed"] is False
    assert "cut off" in result.output["why_not"]
    assert result.unresolved


@pytest.mark.asyncio
async def test_a_file_the_writer_declines_to_change_is_left_alone(monkeypatch):
    import json
    from types import SimpleNamespace

    import app.llm
    from app.agents.schemas import Handoff

    class Fake:
        async def complete(self, message, history=None, memory_context=None):
            return SimpleNamespace(
                text=json.dumps({"changed": False,
                                 "why_not": "This file already does that."}),
                input_tokens=10, output_tokens=5, model="t", provider="mock")

    monkeypatch.setattr(app.llm, "get_provider", lambda settings: Fake())

    result = await dev_patch.run(
        Handoff(task_id=None, workflow_id=None, objective="Write it",
                inputs={"path": "core/thing.py", "current": "VALUE = 1\n"},
                context="", constraints={}, permissions=frozenset()),
        None,
    )
    assert result.output["changed"] is False
    assert result.output["content"] is None


# --- the record ------------------------------------------------------------

@pytest_asyncio.fixture
async def clean(db_pool):
    await db_pool.execute("DELETE FROM change_requests; DELETE FROM attachments;")
    yield db_pool
    await db_pool.execute("DELETE FROM change_requests; DELETE FROM attachments;")


@pytest.mark.asyncio
async def test_only_a_proposed_change_can_be_decided_and_only_once(clean):
    from app.dev import records

    request = await records.open_request("Add a thing", "the brief")

    # A plan is not a proposal. Deciding on one would be approving
    # something that does not exist yet.
    assert await records.decide(request["id"], "approve", "user:owner") is None

    await records.update(request["id"], state="proposed", branch="jarvis/add-a-thing")
    first = await records.decide(request["id"], "approve", "user:owner")
    assert first["state"] == "approved"

    again = await records.decide(request["id"], "discard", "user:owner")
    assert again is None, "a second tap overwrote the first decision"


@pytest.mark.asyncio
async def test_approving_records_a_decision_and_merges_nothing(clean):
    """A system that could merge its own changes is one tap away from a
    system that does."""
    from app.dev import records

    request = await records.open_request("Add a thing", "the brief")
    await records.update(request["id"], state="proposed", branch="jarvis/add-a-thing")
    approved = await records.decide(request["id"], "approve", "user:owner")

    assert approved["decided_by"] == "user:owner"
    assert approved["branch"] == "jarvis/add-a-thing"
    # The record says approved and nothing else happened: no merge column,
    # no merged_at, nothing that could be read as "it is in now".
    assert "merged" not in approved
    # That nothing here CAN merge is checked properly by
    # test_nothing_here_can_push_or_merge, which looks for the git
    # subcommand rather than the word -- this file's own docstring says
    # "merge" several times explaining that it does not.


@pytest.mark.asyncio
async def test_the_listing_leaves_the_diffs_out(clean):
    """A listing carrying a hundred thousand characters of patch per row
    would be unusable on the phone it is read on."""
    from app.dev import records

    request = await records.open_request("Add a thing", "the brief")
    await records.update(request["id"], state="proposed", diff="x" * 50_000)

    rows = await records.recent()
    assert rows and "diff" not in rows[0]
    assert rows[0]["title"] == "Add a thing"


@pytest.mark.asyncio
async def test_a_failed_build_keeps_the_plan_it_already_produced(clean):
    from app.dev import records

    request = await records.open_request("Add a thing", "the brief")
    await records.update(request["id"], plan={"summary": "do the thing"})
    await records.update(request["id"], state="failed", reason="the tests broke")

    again = await records.get(request["id"])
    assert again["plan"]["summary"] == "do the thing"
    assert again["state"] == "failed"
