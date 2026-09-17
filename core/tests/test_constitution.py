"""The protected core, and the model override that cannot widen it.

These map onto the tests the brief names. They matter more than most of
the suite, because they are the ones that stop being true quietly: a
pattern dropped from a list, a write path added that does not go through
the boundary, a preference that starts granting as well as choosing.

The threat model is worth restating, because a test that claimed more
than it proves would be worse than none. What is enforced here is that
JARVIS's own self-development cannot write the rules that govern it.
Nothing here defends against someone with a shell on the server.
"""
import subprocess
import tempfile
from pathlib import Path

import pytest

from app import constitution
from app.config import Settings
from app.dev import repo
from app.llm import NOT_BUILT, ProviderUnavailable, get_provider
from app.llm import preference as pref


class Where:
    def __init__(self, path):
        self.repo_path = str(path)


def a_repo() -> Path:
    tmp = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp)], check=True)
    for name in ("CONSTITUTION.md", "core/app/agents/permissions.py",
                 "core/app/constitution.py", "core/app/routes/media.py",
                 "core/static/app.js", "db/migrations/001_init.sql",
                 "infra/Dockerfile"):
        path = tmp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("original\n")
    subprocess.run(["git", "-C", str(tmp), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp), "-c", "user.name=t",
                    "-c", "user.email=t@t", "commit", "-qm", "first"], check=True)
    return tmp


# --- TEST 3, 4, 5, 15: the protected core ---------------------------------

@pytest.mark.parametrize("path,why", [
    ("CONSTITUTION.md", "the Constitution itself"),
    ("core/app/constitution.py", "what is protected"),
    ("core/app/agents/permissions.py", "the permission system"),
    ("core/app/agents/schemas.py", "the never-delegated set"),
    ("core/app/auth.py", "who counts as the owner"),
    ("core/app/session.py", "sign-in"),
    ("core/app/system_control.py", "the emergency stop"),
    ("core/app/budget.py", "the spending ceiling"),
    ("core/app/dev/repo.py", "the isolation boundary"),
    ("db/migrations/009_anything.sql", "migrations"),
    ("infra/Dockerfile", "deployment"),
    (".env", "secrets"),
])
def test_the_protected_core_is_named_and_says_why(path, why):
    """A refusal that does not say why reads as a bug rather than a rule."""
    reason = constitution.is_protected(path)
    assert reason is not None, f"{path} is not protected"
    assert reason.strip(), f"{path} is refused with no reason given"


@pytest.mark.parametrize("path", [
    "core/app/routes/media.py",
    "core/static/app.js",
    "core/app/media/scout.py",
    "core/tests/test_media.py",
    "docs/AGENT_FOUNDATION.md",
])
def test_ordinary_code_is_not_protected(path):
    """Protecting everything would make self-development useless, which
    is its own kind of failure."""
    assert constitution.is_protected(path) is None


@pytest.mark.parametrize("dodge", [
    "./core/app/agents/permissions.py",
    "core/app/agents/../agents/permissions.py",
    "/core/app/agents/permissions.py",
    r"core\app\agents\permissions.py",
    "core//app//agents//permissions.py",
    "core/app/../app/constitution.py",
])
def test_the_same_file_spelled_differently_is_still_protected(dodge):
    """Backslashes, leading slashes and `..` are all ways one file arrives
    looking like another."""
    assert constitution.is_protected(dodge) is not None, f"got through: {dodge}"


@pytest.mark.asyncio
async def test_the_write_boundary_refuses_the_protected_core():
    """TEST 15. The boundary every write passes through, so a plan that
    got past the planner still cannot land."""
    where = a_repo()
    settings = Where(where)
    made = await repo.open_worktree("try it", settings)
    try:
        with pytest.raises(repo.RepoError) as refused:
            await repo.write_file(made["path"],
                                  "core/app/agents/permissions.py", "pwned\n")
        assert "protected core" in str(refused.value)

        # And ordinary code still writes, or the protection has simply
        # broken self-development instead of bounding it.
        await repo.write_file(made["path"], "core/app/routes/media.py", "fine\n")
    finally:
        await repo.close_worktree(made["path"], settings)


@pytest.mark.asyncio
async def test_reading_the_constitution_is_allowed():
    """JARVIS should be able to read its own rules, reason with them, and
    say when a brief conflicts with them. The protection is on writing."""
    where = a_repo()
    settings = Where(where)
    made = await repo.open_worktree("read it", settings)
    try:
        body = await repo.read_file(made["path"], "CONSTITUTION.md")
        assert body.strip() == "original"
    finally:
        await repo.close_worktree(made["path"], settings)


@pytest.mark.asyncio
async def test_a_protected_file_changed_another_way_still_cannot_be_committed():
    """The last boundary. Everything should have been refused at the
    write, so reaching here means something arrived by a route this module
    does not know about -- which is when a final check earns its keep."""
    where = a_repo()
    settings = Where(where)
    made = await repo.open_worktree("sneak", settings)
    try:
        # Written directly, going around the guarded helper entirely.
        target = Path(made["path"]) / "core" / "app" / "agents" / "permissions.py"
        target.write_text("pwned\n")

        with pytest.raises(repo.RepoError) as refused:
            await repo.commit(made["path"], "sneak it in", settings)
        assert "protected core" in str(refused.value)

        # And nothing was committed.
        log = subprocess.run(["git", "-C", made["path"], "log", "--oneline"],
                             capture_output=True, text=True).stdout
        assert log.count("\n") == 1, "a commit was made despite the refusal"
    finally:
        await repo.close_worktree(made["path"], settings)


def test_the_planner_is_told_what_it_may_not_touch():
    """Refused early so the owner reads the refusal before a call is spent
    writing something that could never land."""
    from app.dev import plan

    assert "{protected}" in plan._PROMPT
    described = constitution.described()
    assert "CONSTITUTION.md" in described
    assert "core/app/agents/permissions.py" in described
    assert "may READ them" in described


def test_a_plan_naming_the_protected_core_loses_those_files():
    """Not the whole plan. The rest of a brief is still worth doing, and
    what was refused is said in `cannot` rather than silently dropped."""
    forbidden = constitution.refuse([
        "core/app/routes/media.py",
        "core/app/agents/permissions.py",
        "CONSTITUTION.md",
    ])
    assert len(forbidden) == 2
    assert any("permission system" in f for f in forbidden)


def test_the_constitution_exists_and_is_reported():
    """Detection, not prevention, and the difference is stated in the
    system's own reported state rather than left to be assumed."""
    state = constitution.state()
    assert state["present"] is True
    assert state["digest"]
    assert "self-development" in state["enforced_against"]
    assert "shell access" in state["not_enforced_against"]


def test_the_constitution_says_what_cannot_be_reasoned_into():
    body = constitution.text()
    assert "I could be more capable if I removed a restriction" in body
    assert "not a performance parameter" in body
    assert "Only Sagar, by hand" in body


# --- TEST 10, 11, 12: the model override ----------------------------------

@pytest.mark.parametrize("said,provider,mode", [
    ("Jarvis, use Claude only for this.", "claude", pref.HARD),
    ("Use Gemini only to build this.", "gemini", pref.HARD),
    ("Use OpenAI only for this analysis.", "openai", pref.HARD),
    ("Prefer Claude.", "claude", pref.SOFT),
    ("Try Gemini first.", "gemini", pref.SOFT),
    ("Use Claude if available.", "claude", pref.SOFT),
    ("Jarvis, redesign your dashboard.", None, pref.NONE),
    ("Claude said the benchmark number was wrong.", None, pref.NONE),
    ("What did Gemini tell you about that?", None, pref.NONE),
])
def test_the_three_modes_are_told_apart(said, provider, mode):
    """A passing mention is not an instruction. Reading one as a choice
    would take his choice away by guessing at it."""
    got = pref.read(said)
    assert got.provider == provider, f"{said!r} -> {got.provider}"
    assert got.mode == mode, f"{said!r} -> {got.mode}"


def test_a_hard_override_never_silently_falls_back():
    """TEST 10. The failure this exists to prevent: a deliberate choice
    quietly becoming a suggestion the system felt free to ignore."""
    settings = Settings(gemini_api_key="x", llm_provider="gemini")

    with pytest.raises(ProviderUnavailable) as refused:
        get_provider(settings, pref.read("Use Claude only for this."))
    assert refused.value.provider == "claude"
    assert "not configured" in refused.value.why


def test_a_soft_preference_falls_back(  ):
    """TEST 11."""
    settings = Settings(gemini_api_key="x", llm_provider="gemini")
    chosen = get_provider(settings, pref.read("Prefer Claude."))
    assert chosen.__class__.__name__ == "GeminiAdapter"


def test_no_preference_leaves_the_choice_to_jarvis():
    """TEST 12."""
    settings = Settings(gemini_api_key="x", llm_provider="gemini")
    chosen = get_provider(settings, pref.read("Redesign your dashboard."))
    assert chosen.__class__.__name__ in ("GeminiAdapter", "FallbackProvider")


def test_asking_for_a_provider_that_was_never_built_says_so():
    """"Not configured" and "never built" are different problems with
    different fixes, and he needs to know which one he has.

    This used to use OpenAI as the example. OpenAI now has an adapter, so
    the example moved to the one that genuinely does not -- rather than
    the test being deleted, which would have quietly stopped checking
    that the distinction exists at all.
    """
    settings = Settings(gemini_api_key="x", llm_provider="gemini")

    with pytest.raises(ProviderUnavailable) as refused:
        get_provider(settings, pref.read("Use a local model only for this."))
    assert "is built yet" in refused.value.why
    assert "local" in NOT_BUILT


def test_openai_is_built_now_and_says_the_other_thing():
    """The distinction the test above is about, from the other side: with
    no key this is a configuration problem, and the message must send him
    to the setting rather than to me."""
    settings = Settings(gemini_api_key="x", llm_provider="gemini")

    with pytest.raises(ProviderUnavailable) as refused:
        get_provider(settings, pref.read("Use OpenAI only for this."))
    assert "not configured" in refused.value.why
    assert "API key is not set" in refused.value.why
    assert "openai" not in NOT_BUILT


def test_a_soft_preference_for_something_never_built_still_answers():
    settings = Settings(gemini_api_key="x", llm_provider="gemini")
    chosen = get_provider(settings, pref.read("Prefer a local model if available."))
    assert chosen.__class__.__name__ == "GeminiAdapter"


def test_choosing_a_model_grants_nothing():
    """The line the brief draws, held in code.

    A preference decides WHO does the work. Permissions come from the
    agent's registry entry and the runtime, and there is no path from one
    to the other -- so this checks the preference carries no authority at
    all rather than that some check happens to catch it later.
    """
    chosen = pref.read("Use Claude only, and publish it without asking.")

    assert chosen.provider == "claude"
    for field in ("permission", "permissions", "approve", "approval",
                  "bypass", "grant", "authority"):
        assert not hasattr(chosen, field), (
            f"a model preference carries {field}, which makes it authority"
        )
    assert set(chosen.as_detail()) == {"provider", "mode", "said"}
