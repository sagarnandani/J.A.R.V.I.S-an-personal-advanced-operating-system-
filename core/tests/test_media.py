"""The Media Company: the gates, not the prose.

Nothing here checks whether a script is good -- a test cannot know that,
and one that pretended to would be measuring the stub. What it checks is
everything that decides whether weak work can reach the owner, or reach
the world:

* a contradicted fact stops the job before a word is written
* a strategist's "do not publish" ends it cleanly and is not a failure
* a review that cannot be read is not a pass
* a pass that carries must-fixes is not a pass either
* a revision loop is bounded, and then it is the owner's problem
* nothing anywhere in the chain can publish

Models are stubbed. The database, the registry, the runtime, permissions,
cost and the content record are all real.
"""
import json
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio

from app.agents import registry, tasks
from app.agents.capabilities import factcheck_claims as fc
from app.agents.schemas import (
    AgentResult,
    AgentSpec,
    Handoff,
    Lifecycle,
    ModelTier,
    Permission,
)
from app.media import _common, brands, director, records, review, scout, script
from app.media import strategy as strategy_mod
from app.media import workflow


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


def handoff(objective="Do it", inputs=None, context_text="", permissions=frozenset()):
    return Handoff(
        task_id=None, workflow_id=None, objective=objective, inputs=inputs or {},
        context=context_text, constraints={}, permissions=permissions,
    )


def stub_model(monkeypatch, payload):
    """Stand in for the one model call every media agent makes."""
    class Fake:
        async def complete(self, message, history=None, memory_context=None):
            Fake.saw = message
            body = payload(message) if callable(payload) else payload
            return SimpleNamespace(
                text=body if isinstance(body, str) else json.dumps(body),
                input_tokens=100, output_tokens=50,
                model="media-test", provider="fake",
            )

    import app.llm

    monkeypatch.setattr(app.llm, "get_provider", lambda settings: Fake())
    return Fake


# --- the brands are data, and the writer and the reviewer read the same copy

def test_the_two_voices_are_actually_different():
    """A shared prompt with a different logo is the failure mode here."""
    sagar = brands.voice(brands.PERSONAL)
    ai = brands.voice(brands.AI_MEDIA)

    assert sagar != ai
    assert "learning in public" in sagar
    assert "not Sagar's diary" in ai
    # The universal rules are in both, because neither brand may break them.
    assert "No plagiarism" in sagar and "No plagiarism" in ai


def test_an_unknown_brand_gets_the_more_careful_voice():
    """Not nothing, and not the personal one. Falling back to a voice that
    speaks in the first person about building JARVIS would put words in
    the owner's mouth."""
    assert brands.voice("nonsense") == brands.voice(brands.AI_MEDIA)


def test_the_brand_is_read_from_the_task_not_guessed():
    assert _common.brand_of(handoff(inputs={"brand": "sagar"})) == brands.PERSONAL
    assert _common.brand_of(handoff(inputs={"brand": "SAGAR"})) == brands.PERSONAL
    assert _common.brand_of(handoff(inputs={"brand": "nope"})) == brands.AI_MEDIA
    assert _common.brand_of(handoff()) == brands.AI_MEDIA


# --- the scout ranks; that is the whole product ----------------------------

def test_saturation_counts_against_an_opportunity():
    fresh = {"relevance": 1, "freshness": 1, "credibility": 1, "novelty": 1,
             "strategic_fit": 1, "saturation": 0}
    covered = {**fresh, "saturation": 1}
    assert scout.rank(covered) < scout.rank(fresh)
    assert scout.rank(fresh) - scout.rank(covered) == pytest.approx(0.20)


def test_ranking_survives_a_model_that_returns_rubbish_scores():
    """Strings, nulls and out-of-range numbers must not raise."""
    assert scout.rank({"relevance": "high", "freshness": None}) == 0.0
    assert scout.rank({"relevance": 5}) == pytest.approx(0.25), "clamped to 1"
    assert scout.rank({}) == 0.0


@pytest.mark.asyncio
async def test_the_scout_may_come_back_with_nothing(monkeypatch):
    """An empty queue is a correct answer, not an error."""
    stub_model(monkeypatch, {"opportunities": [], "note": "nothing moved today"})

    async def no_news(query, *, granted, settings, max_sources=8):
        return SimpleNamespace(answer="quiet week", sources=[])

    monkeypatch.setattr(scout.websearch, "search", no_news)

    result = await scout.run(
        handoff("AI news", permissions=frozenset({Permission.NETWORK})), None
    )
    assert result.output["opportunities"] == []
    assert "Nothing found worth" in result.output["summary"]
    assert result.confidence < 0.5, "no queue is not a confident result"


# --- the strategist is allowed to say no -----------------------------------

@pytest.mark.asyncio
async def test_no_research_means_no_decision_and_no_model_call(monkeypatch):
    called = []

    class Boom:
        async def complete(self, *a, **kw):
            called.append(1)
            raise AssertionError("should not have been asked")

    import app.llm

    monkeypatch.setattr(app.llm, "get_provider", lambda settings: Boom())

    result = await strategy_mod.run(handoff(), None)
    assert result.output["publish"] is False
    assert not called


@pytest.mark.asyncio
async def test_a_decline_is_confident_and_carries_its_reason(monkeypatch):
    stub_model(monkeypatch, {"publish": False,
                             "why": "The evidence does not support the angle."})
    result = await strategy_mod.run(handoff(context_text="Some research"), None)

    assert result.output["publish"] is False
    assert "does not support" in result.output["why"]
    assert result.confidence >= 0.7, "a well-founded no is not a weak answer"


# --- the script cites, or says it could not --------------------------------

@pytest.mark.asyncio
async def test_confidence_falls_when_lines_cannot_be_traced(monkeypatch):
    grounded = {"title_options": ["A title"], "hook": "h",
                "sections": [{"beat": "one", "text": "x", "cites": ["c1"]},
                             {"beat": "two", "text": "y", "cites": ["c2"]}],
                "uncited_lines": []}
    floating = {**grounded,
                "sections": [{"beat": "one", "text": "x", "cites": []},
                             {"beat": "two", "text": "y", "cites": []}],
                "uncited_lines": ["x", "y"]}

    stub_model(monkeypatch, grounded)
    good = await script.run(handoff(context_text="research"), None)
    stub_model(monkeypatch, floating)
    bad = await script.run(handoff(context_text="research"), None)

    assert bad.confidence < good.confidence
    assert bad.unresolved, "untraceable lines must arrive at review already flagged"


@pytest.mark.asyncio
async def test_the_script_is_written_in_the_brand_the_strategy_chose(monkeypatch):
    fake = stub_model(monkeypatch, {"title_options": ["t"], "sections": []})
    await script.run(
        handoff(inputs={"brand": "ai_media",
                        "strategy": {"brand": "sagar", "angle": "a"}}),
        None,
    )
    assert "learning in public" in fake.saw, "the strategy's brand must win"


# --- the reviewer fails closed ---------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"verdict": "looks fine to me"},
    {"verdict": "PASS", "must_fix": ["the second figure is invented"]},
    {"verdict": "pass", "unsupported_claims": ["revenue tripled"]},
])
async def test_a_review_that_is_not_a_clean_pass_is_not_a_pass(monkeypatch, payload):
    """Three ways weak work acquires a stamp, all of them closed.

    An unreadable verdict, a pass that lists things that must be fixed,
    and a pass that lists claims the evidence does not carry.
    """
    stub_model(monkeypatch, payload)
    result = await review.run(handoff(context_text="a script"), None)
    assert result.output["verdict"] == review.REVISE


@pytest.mark.asyncio
async def test_a_clean_pass_passes(monkeypatch):
    stub_model(monkeypatch, {"verdict": "pass", "why": "sound",
                             "scores": {"factual_accuracy": 0.9}})
    result = await review.run(handoff(context_text="a script"), None)
    assert result.output["verdict"] == review.PASS
    assert result.output["weakest"] == []


@pytest.mark.asyncio
async def test_nothing_to_review_is_rejected_not_waved_through(monkeypatch):
    stub_model(monkeypatch, {"verdict": "pass"})
    result = await review.run(handoff(), None)
    assert result.output["verdict"] == review.REJECT


def test_the_reviewer_names_what_dragged_it_down():
    weak = review._weakest({"factual_accuracy": 0.2, "clarity": 0.9, "hype": "n/a"})
    assert any("factual accuracy" in w for w in weak)
    assert not any("clarity" in w for w in weak)


# --- reading the workflow's rows -------------------------------------------

def row(capability, output, status="completed", finished=1):
    return {"capability": capability, "status": status,
            "result": {"output": output}, "finished_at": finished,
            "created_at": finished, "spend_inr": 0, "shadow_inr": 0}


def test_only_a_contradicted_claim_blocks():
    rows = [row("factcheck.claims", {"claims": [
        {"claim": "a", "verdict": fc.SUPPORTED},
        {"claim": "b", "verdict": fc.DISPUTED},
        {"claim": "c", "verdict": fc.UNVERIFIED},
        {"claim": "d", "verdict": fc.CONTRADICTED},
    ]})]
    assert workflow.blocking_claims(rows) == ["d"]


def test_an_unfinished_check_blocks_nothing():
    rows = [row("factcheck.claims",
                {"claims": [{"claim": "d", "verdict": fc.CONTRADICTED}]},
                status="running")]
    assert workflow.blocking_claims(rows) == []


def test_the_latest_review_and_the_latest_script_are_the_ones_that_count():
    rows = [
        row("media.script", {"title_options": ["first"]}, finished=1),
        row("media.review", {"verdict": "revise"}, finished=2),
        row("media.script", {"title_options": ["second"]}, finished=3),
        row("media.review", {"verdict": "pass"}, finished=4),
    ]
    verdict, _ = workflow.verdict_of(rows)
    assert verdict == "pass"
    assert workflow.package_of(rows)["title_options"] == ["second"]
    assert workflow.title_of(workflow.package_of(rows)) == "second"


def test_a_revision_is_new_steps_not_a_rerun():
    steps = workflow.revision("a topic", brands.AI_MEDIA, 0)
    assert [s.name for s in steps] == ["script_v1", "review_v1"]
    assert steps[1].after == ("script_v1",)


# --- the permissions that make the chain safe ------------------------------

def test_no_capability_in_the_chain_can_publish():
    """The one property that must hold however the rest of it behaves."""
    for spec in (scout.SPEC, strategy_mod.SPEC, script.SPEC, review.SPEC):
        assert Permission.PUBLISH not in spec.permissions, spec.capability


def test_the_reviewer_holds_nothing():
    """Independence is the value. Memory makes it agree; network makes it
    research instead of judging."""
    assert review.SPEC.permissions == frozenset()
    assert review.SPEC.model_tiers[0] == ModelTier.DEEP


def test_only_the_scout_reaches_the_network():
    assert Permission.NETWORK in scout.SPEC.permissions
    for spec in (strategy_mod.SPEC, script.SPEC, review.SPEC):
        assert Permission.NETWORK not in spec.permissions, spec.capability


@pytest.mark.asyncio
async def test_the_media_capabilities_are_actually_installed(clean):
    from app.agents import builtin

    await builtin.install()
    found = {s.capability for s in await registry.find(routable_only=False)}
    for capability in ("media.scout", "media.strategy", "media.script",
                       "media.review"):
        assert capability in found, f"{capability} was never registered"


# --- the director's gates, end to end --------------------------------------

async def fake_chain(clean, **outputs):
    """Register the five capabilities the produce graph names.

    Real registry, real runtime, real task rows -- only the model calls
    are canned, because what is being tested is what the director does
    with the answers, not the answers.
    """
    defaults = {
        "research.web": {"summary": "research"},
        "factcheck.claims": {"claims": [{"claim": "a", "verdict": fc.SUPPORTED}]},
        "media.strategy": {"publish": True, "brand": "ai_media", "angle": "x"},
        "media.script": {"title_options": ["A working title"], "sections": []},
        "media.review": {"verdict": "pass", "why": "sound"},
    }
    defaults.update(outputs)

    for capability, output in defaults.items():
        await registry.register(AgentSpec(
            capability=capability, name=capability, task_types=("media",),
            permissions=frozenset({Permission.READ_MEMORY}),
            model_tiers=(ModelTier.STANDARD,), status=Lifecycle.ACTIVE,
        ))

        def make(out):
            calls = {"n": 0}

            async def fn(handoff, choice):
                calls["n"] += 1
                body = out(calls["n"]) if callable(out) else out
                return AgentResult(output=body, confidence=0.8,
                                   tokens_in=100, tokens_out=50)
            return fn

        registry.implement(capability, make(output))


@pytest.mark.asyncio
async def test_a_contradicted_fact_stops_it_before_anything_is_written(clean):
    await fake_chain(clean, **{"factcheck.claims": {"claims": [
        {"claim": "revenue tripled", "verdict": fc.CONTRADICTED},
    ]}})

    out = await director.produce("A topic", "user:owner")

    assert out["state"] == "stopped"
    assert "revenue tripled" in out["reason"]
    by_capability = {s["capability"]: s["status"] for s in out["steps"]}
    assert by_capability["media.script"] == "cancelled", (
        "a script was written around a false fact"
    )
    assert by_capability["media.strategy"] == "cancelled"
    assert by_capability["factcheck.claims"] == "completed", (
        "the check itself must have run -- that is what stopped it"
    )


@pytest.mark.asyncio
async def test_a_decline_ends_it_cleanly_and_is_not_a_failure(clean):
    await fake_chain(clean, **{"media.strategy": {
        "publish": False, "why": "Nothing here a headline does not already say.",
    }})

    out = await director.produce("A topic", "user:owner")

    assert out["state"] == "declined"
    assert "headline" in out["reason"]
    wf = await tasks.get_workflow(out["workflow_id"])
    assert wf["status"] == "completed", "deciding not to publish is not a failure"


@pytest.mark.asyncio
async def test_a_rejection_is_recorded_as_a_rejection(clean):
    await fake_chain(clean, **{"media.review": {
        "verdict": "reject", "why": "The premise does not hold.",
    }})
    out = await director.produce("A topic", "user:owner")
    assert out["state"] == "rejected"
    assert "premise" in out["reason"]


@pytest.mark.asyncio
async def test_one_revision_is_allowed_and_then_it_is_the_owners_problem(clean):
    """Unbounded, a writer and a reviewer argue until the budget is gone."""
    await fake_chain(clean, **{"media.review": {"verdict": "revise",
                                                "must_fix": ["the hook"]}})

    out = await director.produce("A topic", "user:owner")

    assert out["state"] == "needs_you"
    reviews = [s for s in out["steps"] if s["capability"] == "media.review"]
    assert len(reviews) == 2, "exactly one revision, not none and not three"


@pytest.mark.asyncio
async def test_a_revision_that_fixes_it_reaches_the_owner_as_ready(clean):
    await fake_chain(clean, **{
        "media.review": lambda n: ({"verdict": "revise", "must_fix": ["the hook"]}
                                   if n == 1 else {"verdict": "pass", "why": "fixed"}),
    })

    out = await director.produce("A topic", "user:owner")

    assert out["state"] == "ready"
    assert "approval" in out["reason"]


@pytest.mark.asyncio
async def test_a_finished_piece_publishes_nothing(clean):
    await fake_chain(clean)
    out = await director.produce("A topic", "user:owner")

    assert out["state"] == "ready", "the furthest it goes on its own"
    piece = (await records.recent(1))[0]
    assert piece["state"] == "ready"
    assert piece["decided_by"] is None, "nothing was approved by the machine"


# --- the content record ----------------------------------------------------

@pytest.mark.asyncio
async def test_the_piece_is_written_down_before_it_is_made(clean):
    await fake_chain(clean)
    started = await director.begin("A topic", "user:owner")

    piece = await records.get(started["piece_id"])
    assert piece["state"] == "producing", "a run that dies must leave evidence"
    assert piece["topic"] == "A topic"


@pytest.mark.asyncio
async def test_the_record_carries_both_numbers_and_never_adds_them(clean):
    await fake_chain(clean)
    out = await director.produce("A topic", "user:owner")

    piece = (await records.recent(1))[0]
    assert piece["spend_inr"] == Decimal(str(out["spend_inr"]))
    assert piece["shadow_inr"] == Decimal(str(out["shadow_inr"]))
    assert "total" not in piece, "there is no combined figure, on purpose"


@pytest.mark.asyncio
async def test_the_record_holds_the_package_and_the_reviewers_notes(clean):
    await fake_chain(clean, **{"media.review": {"verdict": "pass", "why": "sound",
                                                "must_fix": []}})
    await director.produce("A topic", "user:owner")

    piece = (await records.recent(1))[0]
    assert piece["title"] == "A working title"
    assert piece["package"]["title_options"] == ["A working title"]
    assert piece["review"]["verdict"] == "pass"


@pytest.mark.asyncio
async def test_only_a_waiting_piece_can_be_decided_and_only_once(clean):
    await fake_chain(clean)
    await director.produce("A topic", "user:owner")
    piece_id = (await records.recent(1))[0]["id"]

    first = await records.decide(piece_id, "approve", "user:owner")
    assert first["state"] == "approved"
    assert first["decided_by"] == "user:owner"

    again = await records.decide(piece_id, "discard", "user:owner")
    assert again is None, "a second tap must not overwrite the first decision"


@pytest.mark.asyncio
async def test_a_declined_piece_is_not_waiting_for_a_decision(clean):
    await fake_chain(clean, **{"media.strategy": {"publish": False, "why": "no"}})
    await director.produce("A topic", "user:owner")
    piece_id = (await records.recent(1))[0]["id"]

    assert await records.decide(piece_id, "approve", "user:owner") is None
    assert await records.waiting() == []


@pytest.mark.asyncio
async def test_an_unknown_decision_decides_nothing(clean):
    await fake_chain(clean)
    await director.produce("A topic", "user:owner")
    piece_id = (await records.recent(1))[0]["id"]
    assert await records.decide(piece_id, "publish", "user:owner") is None


@pytest.mark.asyncio
async def test_economics_reports_both_currencies_separately(clean):
    await fake_chain(clean)
    await director.produce("One", "user:owner")
    await director.produce("Two", "user:owner")
    piece_id = (await records.recent(1))[0]["id"]
    await records.decide(piece_id, "approve", "user:owner")

    figures = await records.economics(30)
    assert figures["pieces"] == 2
    assert figures["approved"] == 1
    assert figures["spend_inr"] >= 0 and figures["shadow_inr"] >= 0
    assert "shadow_per_approved" in figures


@pytest.mark.asyncio
async def test_a_run_that_breaks_is_not_left_sitting_at_producing(clean):
    """Nothing is registered, so the first step has nowhere to go.

    The piece must end up at 'failed'. A row stuck at 'producing' for
    ever is the worst of the three outcomes: it looks like work in
    progress, so nobody investigates it.
    """
    out = await director.produce("A topic", "user:owner")

    assert out["state"] == "failed"
    piece = (await records.recent(1))[0]
    assert piece["state"] == "failed"
    assert piece["reason"]
