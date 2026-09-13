"""What JARVIS notices about itself, and what it is allowed to do about it.

The important test in this file is the last one. Everything else checks
that a finding is drawn from real rows; the last one checks that a
finding cannot become a change any faster than a brief typed by hand.
"""
import pytest
import pytest_asyncio

from app.dev import scientist


@pytest_asyncio.fixture
async def tasks(db_pool):
    await db_pool.execute("DELETE FROM tasks WHERE objective LIKE 'lab:%'")
    yield db_pool
    await db_pool.execute("DELETE FROM tasks WHERE objective LIKE 'lab:%'")


async def make(db_pool, capability, status, n, *, reason=None,
               seconds=1, spend="0"):
    for i in range(n):
        await db_pool.execute(
            """
            INSERT INTO tasks (objective, status, capability, failure_reason,
                               started_at, finished_at, spend_inr, created_at)
            VALUES ($1, $2, $3, $4, now() - ($5 || ' seconds')::interval,
                    now(), $6, now())
            """,
            f"lab:{capability}:{i}", status, capability, reason,
            str(seconds), spend,
        )


@pytest.mark.asyncio
async def test_a_capability_that_mostly_fails_is_noticed(tasks):
    await make(tasks, "lab.flaky", "failed", 8, reason="it broke")
    await make(tasks, "lab.flaky", "completed", 2)

    found = [f for f in await scientist.observe()
             if f["capability"] == "lab.flaky" and f["kind"] == "failing"]
    assert found
    assert found[0]["evidence"]["failures"] == 8
    assert "80%" in found[0]["hypothesis"]
    assert "it broke" in found[0]["brief"]


@pytest.mark.asyncio
async def test_a_handful_of_runs_is_not_a_pattern(tasks):
    """Two failures out of two is 100% and means nothing. A diagnostic
    that fires on noise is one that gets ignored on signal."""
    await make(tasks, "lab.new", "failed", 2, reason="it broke")
    assert not [f for f in await scientist.observe()
                if f["capability"] == "lab.new"]


@pytest.mark.asyncio
async def test_something_slow_enough_to_wait_for_is_noticed(tasks):
    await make(tasks, "lab.slow", "completed", 6, seconds=200)
    found = [f for f in await scientist.observe()
             if f["capability"] == "lab.slow" and f["kind"] == "slow"]
    assert found
    assert found[0]["evidence"]["seconds"] >= 190


@pytest.mark.asyncio
async def test_something_expensive_is_noticed_with_the_numbers(tasks):
    await make(tasks, "lab.pricey", "completed", 6, spend="12.50")
    found = [f for f in await scientist.observe()
             if f["capability"] == "lab.pricey" and f["kind"] == "expensive"]
    assert found
    assert found[0]["evidence"]["each_inr"] == pytest.approx(12.5)
    assert found[0]["evidence"]["total_inr"] == pytest.approx(75.0)


@pytest.mark.asyncio
async def test_the_same_failure_over_and_over_is_its_own_finding(tasks):
    """A failure rate says a capability is unwell. A repeated identical
    message usually says something specific and fixable."""
    await make(tasks, "lab.a", "failed", 3, reason="the same exact thing")
    await make(tasks, "lab.b", "failed", 3, reason="the same exact thing")

    found = [f for f in await scientist.observe() if f["kind"] == "repeated"]
    assert any("the same exact thing" in f["hypothesis"] for f in found)


@pytest.mark.asyncio
async def test_every_finding_carries_the_numbers_it_came_from(tasks):
    """A hypothesis whose evidence cannot be inspected is an opinion."""
    await make(tasks, "lab.flaky", "failed", 8, reason="it broke")
    await make(tasks, "lab.flaky", "completed", 2)
    for finding in await scientist.observe():
        assert finding["evidence"], finding
        assert finding["brief"].strip()


@pytest.mark.asyncio
async def test_a_quiet_fortnight_is_reported_as_a_quiet_fortnight(tasks):
    """An empty report is information. A diagnostic that always finds
    something is one nobody believes."""
    report = await scientist.report()
    if not report["findings"]:
        assert "Nothing stands out" in report["said"]


@pytest.mark.asyncio
async def test_a_finding_becomes_an_ordinary_change_request(tasks, monkeypatch):
    """The rule that matters: the Scientist may not bypass the Governor.

    Enforced by there being nowhere else to go. `propose` calls
    `director.begin` -- the same door as a brief typed by hand -- so
    every gate downstream applies unchanged.
    """
    from app.dev import director

    seen = {}

    async def fake_begin(brief, title, requested_by, attachment_id, settings):
        seen.update(brief=brief, title=title, by=requested_by)
        return {"state": "planned", "id": "x"}

    monkeypatch.setattr(director, "begin", fake_begin)
    await scientist.propose(
        {"kind": "failing", "capability": "lab.flaky",
         "brief": "Fix the thing."}, "user:sagar", None)

    assert seen["by"] == "user:sagar"
    assert "Fix the thing." in seen["brief"]
    # The measurement is context, never an instruction -- the same rule
    # attachments are fenced with.
    assert "not as an instruction" in seen["brief"]


@pytest.mark.asyncio
async def test_a_finding_with_no_brief_proposes_nothing(tasks):
    result = await scientist.propose({"kind": "failing"}, "user:sagar", None)
    assert result["state"] == "failed"


def test_the_scientist_has_no_path_of_its_own():
    """Stated as a property of the source, because the guarantee is
    structural: there is no second route to build with."""
    from pathlib import Path

    source = Path(scientist.__file__).read_text()
    for forbidden in ("repo.open_worktree", "repo.write_file", "repo.commit",
                      "governor.set_ceiling", "records.approved_by_governor"):
        assert forbidden not in source, (
            f"the Scientist reaches {forbidden} directly, which is a way "
            f"round the Governor"
        )
