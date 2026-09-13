"""The boundary itself: what JARVIS may approve, and what it may never.

The Governor is one number and one classification. Almost every test here
is about the ways that could be wrong in the direction that matters --
something judged safer than it is.
"""
import pytest

from app import constitution, governor


# --- classifying ------------------------------------------------------------

@pytest.mark.parametrize("path,level", [
    # Level 4: the protected core, whatever else is true about it.
    ("CONSTITUTION.md", 4),
    ("core/app/constitution.py", 4),
    ("core/app/agents/permissions.py", 4),
    ("core/app/governor.py", 4),
    ("core/app/dev/auditor.py", 4),
    ("core/app/auth.py", 4),
    ("core/app/budget.py", 4),
    ("db/migrations/001_init.sql", 4),
    ("infra/Dockerfile", 4),
    (".env.production", 4),
    # Level 3: a mistake here is felt everywhere.
    ("core/app/agents/orchestrator.py", 3),
    ("core/app/agents/runtime.py", 3),
    ("core/app/llm/gemini.py", 3),
    ("core/app/memory.py", 3),
    ("core/app/scheduler.py", 3),
    ("core/app/main.py", 3),
    ("core/requirements.txt", 3),
    ("core/app/dev/plan.py", 3),
    (".github/workflows/ci.yml", 3),
    # Level 2: ordinary code.
    ("core/app/media/script.py", 2),
    ("core/app/routes/media.py", 2),
    ("core/static/app.js", 2),
    ("core/tests/test_media.py", 2),
    # Level 1: words and appearance.
    ("README.md", 1),
    ("docs/DEPLOYMENT.md", 1),
    ("core/static/look.css", 1),
])
def test_each_path_lands_where_it_should(path, level):
    assert governor.level_of(path)[0] == level, (
        f"{path} was classified {governor.level_of(path)[0]} "
        f"({governor.level_of(path)[1]}), expected {level}"
    )


def test_the_riskiest_file_decides_the_whole_change():
    """Not an average, not a majority.

    A change that edits forty stylesheets and one file that decides who
    the owner is, is a change that decides who the owner is.
    """
    verdict = governor.classify([
        "README.md", "docs/a.md", "core/static/look.css",
        "core/app/agents/orchestrator.py",
    ])
    assert verdict.level == 3
    assert any("orchestrator" in r for r in verdict.reasons)


def test_a_change_that_writes_nothing_is_level_zero():
    assert governor.classify([]).level == 0
    assert governor.classify(None).level == 0


def test_an_unrecognised_path_is_not_assumed_harmless():
    """The safe direction to be wrong in.

    Something nobody thought about is ordinary code, never documentation.
    A default of 1 would mean every new kind of file arrives pre-approved.
    """
    level, _ = governor.level_of("core/app/something_invented_later.py")
    assert level >= 2


@pytest.mark.parametrize("dodge", [
    "core//app//constitution.py",
    "./core/app/constitution.py",
    "/core/app/constitution.py",
    "core\\\\app\\\\constitution.py",
])
def test_spelling_a_protected_path_differently_does_not_lower_it(dodge):
    assert governor.level_of(dodge)[0] == 4


def test_everything_the_constitution_protects_is_level_four():
    """The two lists cannot drift apart.

    The Governor classifies and the Constitution refuses. If a path were
    protected but classified 3, it would be offered for autonomous
    approval and then refused at the write boundary -- confusing, and a
    sign the two had stopped agreeing.
    """
    for pattern, _ in constitution.PROTECTED:
        sample = pattern.replace("*", "x")
        assert governor.level_of(sample)[0] == 4, (
            f"{sample} is protected but classified "
            f"{governor.level_of(sample)[0]}"
        )


def test_the_governor_and_the_auditor_are_themselves_protected():
    """Whoever can rewrite the classifier decides their own risk level,
    and whoever can rewrite the auditor decides their own review."""
    assert constitution.is_protected("core/app/governor.py")
    assert constitution.is_protected("core/app/dev/auditor.py")


# --- deciding ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_ceiling_starts_at_nothing(db_pool):
    await governor.set_ceiling(0, "test")
    assert await governor.ceiling() == 0


@pytest.mark.asyncio
async def test_the_ceiling_can_never_reach_the_protected_core(db_pool):
    """Clamped rather than rejected, so an attempt to set 4 lands on 3
    instead of failing somewhere upstream and leaving the old value."""
    assert await governor.set_ceiling(4, "test") == 3
    assert await governor.set_ceiling(99, "test") == 3
    assert await governor.set_ceiling(-1, "test") == 0
    await governor.set_ceiling(0, "test")


@pytest.mark.asyncio
async def test_the_database_refuses_level_four_too(db_pool):
    """Two independent refusals. If `set_ceiling`'s clamp were ever
    removed, the column constraint still stands."""
    import asyncpg

    with pytest.raises(asyncpg.CheckViolationError):
        await db_pool.execute(
            "UPDATE governor_policy SET max_autonomous_level = 4 WHERE id"
        )


@pytest.mark.asyncio
async def test_the_protected_core_is_refused_at_every_setting(db_pool):
    for level in (0, 1, 2, 3):
        await governor.set_ceiling(level, "test")
        decision = await governor.review(
            paths=["core/app/agents/permissions.py"], tests_passed=True)
        assert decision.outcome == "refused", f"at ceiling {level}"
    await governor.set_ceiling(0, "test")


@pytest.mark.asyncio
async def test_within_the_ceiling_and_clean_it_approves(db_pool):
    await governor.set_ceiling(2, "test")
    decision = await governor.review(paths=["core/app/media/script.py"],
                                     tests_passed=True)
    assert decision.outcome == "autonomous"
    assert decision.level == 2
    await governor.set_ceiling(0, "test")


@pytest.mark.asyncio
async def test_above_the_ceiling_it_asks(db_pool):
    await governor.set_ceiling(1, "test")
    decision = await governor.review(paths=["core/app/media/script.py"],
                                     tests_passed=True)
    assert decision.outcome == "ask"
    assert "level 1" in decision.why
    await governor.set_ceiling(0, "test")


@pytest.mark.asyncio
async def test_failing_tests_are_never_approved(db_pool):
    await governor.set_ceiling(3, "test")
    decision = await governor.review(paths=["README.md"], tests_passed=False)
    assert decision.outcome == "ask"
    assert "the tests fail" in decision.why
    await governor.set_ceiling(0, "test")


@pytest.mark.asyncio
async def test_tests_that_never_ran_count_as_not_verified(db_pool):
    """Not the same as failing, and treated the same.

    'Nobody checked' must not be the state an autonomous approval is
    granted from -- it is the state every change is in before anyone
    looks at it.
    """
    await governor.set_ceiling(3, "test")
    decision = await governor.review(paths=["README.md"], tests_passed=None)
    assert decision.outcome == "ask"
    assert "did not run" in decision.why
    await governor.set_ceiling(0, "test")


@pytest.mark.asyncio
async def test_an_auditor_finding_stops_approval(db_pool):
    await governor.set_ceiling(3, "test")
    decision = await governor.review(
        paths=["README.md"], tests_passed=True,
        audit={"blocking": ["the change grants a permission"]})
    assert decision.outcome == "ask"
    assert "grants a permission" in decision.why
    await governor.set_ceiling(0, "test")


@pytest.mark.asyncio
async def test_the_emergency_stop_refuses_everything(db_pool, monkeypatch):
    """The stop is not one input among several."""
    from app import system_control

    await governor.set_ceiling(3, "test")

    async def stopped():
        return True

    monkeypatch.setattr(system_control, "is_stopped", stopped)
    decision = await governor.review(paths=["README.md"], tests_passed=True)
    assert decision.outcome == "refused"
    assert "stopped" in decision.why
    await governor.set_ceiling(0, "test")


@pytest.mark.asyncio
async def test_the_refusal_says_which_file_caused_it(db_pool):
    """A refusal that does not name the file reads as a bug and gets
    worked around rather than understood."""
    decision = await governor.review(
        paths=["core/app/media/script.py", "core/app/auth.py"],
        tests_passed=True)
    assert decision.outcome == "refused"
    assert "core/app/auth.py" in decision.why
    assert "core/app/media/script.py" not in decision.why
