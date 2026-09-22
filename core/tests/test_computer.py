"""Running something on the owner's own machine (section 25).

He chose the shape himself: a fixed list of safe things JARVIS may run
on its own, and for anything else the exact command shown to him with
nothing happening until he taps yes. So the tests are about the seams of
that sentence.

- Is the list actually a list, or does something get through it?
- Is a parameter a value, or can it become an option?
- Is "anything else" really stopped, and stopped BEFORE it runs?
- Does saying yes to one command say yes to the next one?

The last is the one that would be quiet. A per-category approval would
mean the owner approving `systemctl status nginx` had, without being
told, approved everything else that task felt like running.
"""
import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio

from app import computer
from app.agents.schemas import ApprovalRequired, Permission, PermissionDenied
from app.computer import CannotRun

RUN = frozenset({Permission.RUN_COMMAND, Permission.READ_MEMORY})
ON = SimpleNamespace(computer_access=True)
OFF = SimpleNamespace(computer_access=False)


@pytest_asyncio.fixture
async def quiet(monkeypatch):
    """No audit writes and no emergency stop, so the DB is not needed."""
    async def nothing(*a, **kw):
        return None
    monkeypatch.setattr(computer, "_record", nothing)
    from app import system_control
    monkeypatch.setattr(system_control, "refuse_if_stopped", nothing)
    yield


# --- the switch ------------------------------------------------------------

@pytest.mark.asyncio
async def test_it_is_off_until_the_owner_turns_it_on(quiet):
    with pytest.raises(CannotRun, match="switched off"):
        await computer.run_known("uptime", granted=RUN, settings=OFF)
    with pytest.raises(CannotRun, match="switched off"):
        await computer.run_anything(["uptime"], granted=RUN, settings=OFF)


def test_off_is_the_default():
    from app.config import Settings

    assert Settings().computer_access is False


@pytest.mark.asyncio
async def test_settings_that_do_not_mention_it_at_all_mean_off(quiet):
    """The direction to be wrong in.

    An older settings object, a stub in a test, a half-applied config --
    none of them say anything about this, and "says nothing" must not
    read as "yes".
    """
    silent = SimpleNamespace()
    assert computer._enabled(silent) is False
    with pytest.raises(CannotRun, match="switched off"):
        await computer.run_known("uptime", granted=RUN, settings=silent)


def test_an_action_that_forgets_its_pattern_still_refuses_an_option():
    """PLAIN is the floor, not decoration.

    It spent a while being described as the default and referenced by
    nothing, because every shipped action names its own pattern. The next
    one added might not.
    """
    careless = computer.Action("careless", ("echo", "{thing}"),
                               "echo something", params={"thing": None})
    assert careless.fill({"thing": "hello"}) == ["echo", "hello"]
    for nasty in ("-rf", "--output=/etc/passwd", "a b", "a/b", "$(id)"):
        with pytest.raises(CannotRun, match="not something I will pass"):
            careless.fill({"thing": nasty})


@pytest.mark.asyncio
async def test_it_needs_the_permission_even_when_switched_on(quiet):
    for call in (
        computer.run_known("uptime", granted=frozenset(), settings=ON),
        computer.run_anything(["uptime"], granted=frozenset(), settings=ON),
    ):
        with pytest.raises(PermissionDenied, match="run_command"):
            await call


@pytest.mark.asyncio
async def test_the_emergency_stop_stops_this_too(monkeypatch):
    async def nothing(*a, **kw):
        return None
    monkeypatch.setattr(computer, "_record", nothing)

    from app import system_control

    async def stopped():
        raise RuntimeError("JARVIS is stopped")
    monkeypatch.setattr(system_control, "refuse_if_stopped", stopped)

    with pytest.raises(RuntimeError, match="stopped"):
        await computer.run_known("uptime", granted=RUN, settings=ON)


# --- the list --------------------------------------------------------------

@pytest.mark.asyncio
async def test_something_not_on_the_list_is_not_run_by_run_known(quiet):
    with pytest.raises(CannotRun, match="not one of the things I may run"):
        await computer.run_known("rm", granted=RUN, settings=ON)


def test_everything_on_the_list_only_looks(quiet=None):
    """The list is read-only work, deliberately.

    Changing the machine is the owner's to approve. This is the test that
    notices if an entry ever drifts across that line -- which would be a
    real decision, made in a diff, without anyone saying so.
    """
    changes_things = {
        "rm", "mv", "cp", "dd", "mkfs", "chmod", "chown", "kill", "pkill",
        "reboot", "shutdown", "halt", "apt", "apt-get", "yum", "dnf", "pip",
        "npm", "git", "curl", "wget", "tee", "truncate", "sed", "install",
    }
    changing_verbs = {"restart", "stop", "start", "enable", "disable", "kill",
                      "reload", "rm", "prune", "exec", "run"}
    for action in computer.ALLOWED:
        assert action.argv[0] not in changes_things, (
            f"'{action.name}' runs {action.argv[0]}, which changes things"
        )
        for word in action.argv[1:]:
            assert word not in changing_verbs, (
                f"'{action.name}' passes '{word}', which changes things"
            )


def test_every_action_can_be_explained_to_the_owner():
    for action in computer.ALLOWED:
        assert action.what and not action.what.endswith("."), action.name
        # The catalogue is what he reads. It must say the real command.
        entry = next(e for e in computer.catalogue() if e["name"] == action.name)
        assert entry["command"].startswith(action.argv[0])


def test_no_action_is_assembled_from_a_string():
    """No shell, so no string. A command that is one string is a command
    somebody will eventually build by concatenation."""
    for action in computer.ALLOWED:
        assert isinstance(action.argv, tuple) and len(action.argv) >= 1
        for part in action.argv:
            assert isinstance(part, str)


# --- parameters, which are where the hole would be ------------------------

@pytest.mark.parametrize("nasty", [
    "-rf", "--output=/etc/passwd", "; rm -rf /", "a b", "../../etc/passwd",
    "$(whoami)", "`id`", "a|b", "a>b", "",
])
def test_a_parameter_cannot_become_an_option_or_a_path(nasty):
    action = computer.known("service_status")
    with pytest.raises(CannotRun, match="not something I will pass"):
        action.fill({"unit": nasty})


def test_an_ordinary_unit_name_goes_through():
    assert computer.known("service_status").fill({"unit": "nginx.service"}) == [
        "systemctl", "status", "nginx.service", "--no-pager"]


def test_a_missing_parameter_is_asked_for_rather_than_guessed():
    with pytest.raises(CannotRun, match="needs unit"):
        computer.known("service_status").fill({})
    with pytest.raises(CannotRun, match="needs lines"):
        computer.known("service_log").fill({"unit": "nginx"})


def test_a_line_count_is_a_number_and_bounded():
    log = computer.known("service_log")
    assert log.fill({"unit": "nginx", "lines": "50"})[4] == "50"
    for bad in ("0", "-1", "9999", "all", "1e3"):
        with pytest.raises(CannotRun):
            log.fill({"unit": "nginx", "lines": bad})


# --- actually running one --------------------------------------------------

@pytest.mark.asyncio
async def test_a_listed_action_runs_without_asking(quiet):
    ran = await computer.run_known("uptime", granted=RUN, settings=ON)
    assert ran.ok, ran.stderr
    assert ran.stdout.strip()
    assert ran.asked_first is False


@pytest.mark.asyncio
async def test_there_is_no_shell(quiet):
    """The proof, not the claim.

    Under a shell this writes a file. With exec it is an argument to
    `echo` and nothing happens, which is the entire point.
    """
    import pathlib

    mark = pathlib.Path("/tmp/jarvis-shell-test-marker")
    if mark.exists():
        mark.unlink()

    ran = await computer._spawn(
        ["echo", f"hello; touch {mark}"], timeout=10)
    assert ran.ok
    assert not mark.exists(), "a semicolon started a second command"
    assert str(mark) in ran.stdout, "the argument was not passed literally"


@pytest.mark.asyncio
async def test_the_keys_are_not_handed_to_the_subprocess(quiet, monkeypatch):
    """A subprocess inherits its parent's environment, and this parent's
    holds the model provider keys."""
    monkeypatch.setenv("GEMINI_API_KEY", "super-secret-value")
    monkeypatch.setenv("DATABASE_URL", "postgres://secret@host/db")

    ran = await computer._spawn(["env"], timeout=10)
    assert "super-secret-value" not in ran.stdout
    assert "postgres://secret" not in ran.stdout
    assert "PATH=" in ran.stdout, "the subprocess got no environment at all"


@pytest.mark.asyncio
async def test_a_command_that_hangs_is_killed(quiet):
    with pytest.raises(CannotRun, match="still running"):
        await computer._spawn(["sleep", "30"], timeout=1.0)


@pytest.mark.asyncio
async def test_a_command_that_is_not_installed_says_so(quiet):
    with pytest.raises(CannotRun, match="not installed"):
        await computer._spawn(["definitely-not-a-real-program"], timeout=5)


@pytest.mark.asyncio
async def test_output_is_capped(quiet, monkeypatch):
    monkeypatch.setattr(computer, "MAX_OUTPUT", 100)
    ran = await computer._spawn(
        ["python3", "-c", "print('x' * 5000)"], timeout=10)
    assert len(ran.stdout) <= 100
    assert ran.truncated is True


@pytest.mark.asyncio
async def test_a_failing_command_is_reported_not_raised(quiet):
    ran = await computer._spawn(["false"], timeout=5)
    assert ran.ok is False
    assert ran.exit_code != 0


# --- anything else ---------------------------------------------------------

@pytest.mark.asyncio
async def test_an_unlisted_command_asks_first_and_does_not_run(quiet, monkeypatch):
    spawned = []
    async def never(*a, **kw):
        spawned.append(a)
    monkeypatch.setattr(computer, "_spawn", never)

    with pytest.raises(ApprovalRequired) as raised:
        await computer.run_anything(["systemctl", "restart", "nginx"],
                                    granted=RUN, settings=ON, task_id=uuid4())

    assert not spawned, "it ran the command and then asked"
    assert raised.value.category == computer.CATEGORY
    # The owner has to be able to read exactly what will happen.
    assert "systemctl restart nginx" in str(raised.value)


@pytest.mark.asyncio
async def test_the_ask_carries_the_command_as_data_not_only_as_prose(quiet):
    """What resumes is checked against this, so it cannot be prose."""
    with pytest.raises(ApprovalRequired) as raised:
        await computer.run_anything(["ls", "-la"], granted=RUN, settings=ON,
                                    task_id=uuid4())
    assert raised.value.saw.get("argv") == ["ls", "-la"]


@pytest.mark.asyncio
async def test_with_no_task_there_is_nobody_to_ask_so_nothing_runs(quiet):
    with pytest.raises(ApprovalRequired):
        await computer.run_anything(["ls"], granted=RUN, settings=ON, task_id=None)


@pytest.mark.asyncio
async def test_an_empty_command_is_refused_before_anything_else(quiet):
    for empty in ([], [""], ["   "]):
        with pytest.raises(CannotRun, match="no command here"):
            await computer.run_anything(empty, granted=RUN, settings=ON,
                                        task_id=uuid4())


# --- saying yes, and what exactly it said yes to --------------------------
#
# The quiet failure this whole design turns on. Approval in this system
# is per task and per category, which is right for "publishing" -- the
# owner is approving a piece of work. For a command it is wrong, and
# wrong in a direction nobody would notice: approving `systemctl status
# nginx` would leave that task free to run anything at all.

@pytest_asyncio.fixture
async def clean(db_pool):
    await db_pool.execute(
        "DELETE FROM task_approvals; DELETE FROM agent_metrics; "
        "DELETE FROM agent_events; DELETE FROM tasks; DELETE FROM workflows; "
        "DELETE FROM agents; DELETE FROM audit_log;"
    )
    yield db_pool


async def a_task(pool) -> object:
    from app.agents import tasks

    wf = await tasks.create_workflow("Check the server", "user:owner")
    return await tasks.create(objective="Is nginx up?",
                              capability="system.machine", workflow_id=wf)


async def owner_approves(task_id, argv):
    """What the route does when the owner taps yes."""
    from app.agents import approvals, tasks

    await tasks.await_approval(task_id, "needs your yes", computer.CATEGORY,
                               {"argv": list(argv)})
    row = await tasks.get(task_id)
    return await approvals.decide(
        task_id, row["awaiting_category"], "approved", "user:owner",
        saw={"objective": row["objective"], **(row["awaiting_detail"] or {})},
    )


@pytest.mark.asyncio
async def test_the_task_records_what_it_is_waiting_on(clean, quiet):
    from app.agents import tasks

    task_id = await a_task(clean)
    await tasks.await_approval(task_id, "needs your yes", computer.CATEGORY,
                               {"argv": ["systemctl", "restart", "nginx"]})

    row = await tasks.get(task_id)
    assert row["awaiting_category"] == computer.CATEGORY
    assert row["awaiting_detail"]["argv"] == ["systemctl", "restart", "nginx"]


@pytest.mark.asyncio
async def test_saying_yes_lets_that_command_run(clean, quiet):
    task_id = await a_task(clean)
    await owner_approves(task_id, ["echo", "approved"])

    ran = await computer.run_anything(["echo", "approved"], granted=RUN,
                                      settings=ON, task_id=task_id)
    assert ran.ok and "approved" in ran.stdout
    assert ran.asked_first is True


@pytest.mark.asyncio
async def test_saying_yes_to_one_command_does_not_say_yes_to_another(clean, quiet):
    """The one that would be silent.

    Same task, same category, already approved. A per-category check
    would let this straight through -- and the owner would have no idea
    he had agreed to it.
    """
    task_id = await a_task(clean)
    await owner_approves(task_id, ["systemctl", "status", "nginx"])

    with pytest.raises(ApprovalRequired):
        await computer.run_anything(["rm", "-rf", "/tmp/anything"],
                                    granted=RUN, settings=ON, task_id=task_id)


@pytest.mark.asyncio
async def test_an_approved_command_cannot_grow_an_argument(clean, quiet):
    """Nor shrink one. The vector has to match exactly."""
    task_id = await a_task(clean)
    await owner_approves(task_id, ["ls", "/tmp"])

    for changed in (["ls", "/tmp", "/etc"], ["ls"], ["ls", "/etc"],
                    ["ls", "-la", "/tmp"]):
        with pytest.raises(ApprovalRequired):
            await computer.run_anything(changed, granted=RUN, settings=ON,
                                        task_id=changed and task_id)


@pytest.mark.asyncio
async def test_a_rejection_is_not_an_approval(clean, quiet):
    from app.agents import approvals, tasks

    task_id = await a_task(clean)
    await tasks.await_approval(task_id, "needs your yes", computer.CATEGORY,
                               {"argv": ["echo", "no"]})
    # Recorded with the command in it, exactly as an approval would be.
    # Without that the argv comparison alone would reject this, and the
    # test would pass while saying nothing about whether the DECISION was
    # read at all.
    await approvals.decide(task_id, computer.CATEGORY, "rejected", "user:owner",
                           saw={"argv": ["echo", "no"]})

    assert await computer.approved_exactly(task_id, ["echo", "no"]) is False


@pytest.mark.asyncio
async def test_another_task_s_approval_is_not_this_task_s(clean, quiet):
    mine = await a_task(clean)
    theirs = await a_task(clean)
    await owner_approves(theirs, ["echo", "hello"])

    assert await computer.approved_exactly(theirs, ["echo", "hello"]) is True
    assert await computer.approved_exactly(mine, ["echo", "hello"]) is False


@pytest.mark.asyncio
async def test_every_run_is_written_down(clean):
    """Including the ones nobody had to approve."""
    from app.db import fetch
    from app import system_control

    async def nothing(*a, **kw):
        return None
    import app.computer as mod
    original = mod.system_control if hasattr(mod, "system_control") else None

    await computer.run_known("uptime", granted=RUN, settings=ON)
    rows = await fetch(
        "SELECT actor, action, category, approved_by FROM audit_log "
        "WHERE action LIKE 'ran_command:%'")
    assert len(rows) == 1
    assert rows[0]["action"].endswith("uptime")
    assert rows[0]["category"] != "low_risk", (
        "running something on the owner's machine was filed as low risk"
    )
    assert rows[0]["approved_by"] is None, "a listed action claimed approval"


# --- the boundaries around the boundary ------------------------------------

def test_the_list_is_not_something_jarvis_may_add_to():
    """Otherwise "ask me for anything else" means nothing.

    The first thing a change that wanted more reach would do is widen the
    list, and it would do it in a diff that looked tidy.
    """
    from app.constitution import is_protected

    why = is_protected("core/app/computer.py")
    assert why, "self-development can add to its own allow-list"
    assert "without asking" in why


def test_the_permission_is_explained_in_words_the_owner_would_use():
    from app.agents.org import PERMISSION_WORDS

    said = PERMISSION_WORDS[Permission.RUN_COMMAND]
    assert "your server" in said
    assert "your yes" in said, (
        "the permission is shown without saying that anything off the "
        "list still has to be approved"
    )


def test_running_commands_is_not_folded_into_another_approval_category():
    """Saying yes to publishing a post is not saying yes to the server."""
    from app.agents.schemas import ALWAYS_APPROVED

    assert computer.CATEGORY not in ALWAYS_APPROVED.values()
    assert Permission.RUN_COMMAND not in ALWAYS_APPROVED, (
        "mapping it here would make every listed check ask too, and an "
        "owner asked nine times before breakfast stops reading"
    )


def test_the_machine_capability_cannot_also_reach_the_internet():
    """Run things, or reach the web. Not both in one pair of hands."""
    from app.agents.capabilities import machine

    held = machine.SPEC.permissions
    assert Permission.RUN_COMMAND in held
    assert Permission.NETWORK not in held, (
        "a capability that can run commands AND reach the internet is one "
        "bug away from being a way to send the machine's contents out"
    )
    for never in (Permission.PUBLISH, Permission.EXTERNAL_MESSAGE,
                  Permission.WRITE_FILES, Permission.DELETE,
                  Permission.MODIFY_CONFIG, Permission.MODIFY_AGENTS):
        assert never not in held
