"""factcheck.claims -- the capability that makes confidence mean something.

Gemini is stubbed; everything else is real. What is being tested is the
judgement, not the plumbing: that a verdict with no sources behind it
cannot pass as verified, that confidence is arithmetic rather than
assertion, that one failed lookup does not discard four good ones while
five failed lookups do not masquerade as five honest "unverified"s, and
that a research task's answer really does reach the checker in a chain.
"""
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio

from app.agents import context, orchestrator, registry, runtime, tasks, telemetry
from app.agents.capabilities import factcheck_claims as fc
from app.agents.capabilities import research_web
from app.agents.schemas import (
    AgentError,
    AgentSpec,
    Handoff,
    Lifecycle,
    ModelTier,
    Permission,
    PermissionDenied,
)
from app.agents.tools import websearch
from app.config import Settings

SETTINGS = Settings(gemini_api_key="fake", gemini_model="gemini-test")
NETWORK = frozenset({Permission.NETWORK})


@pytest_asyncio.fixture
async def clean(db_pool):
    await db_pool.execute(
        "DELETE FROM agent_metrics; DELETE FROM agent_events; "
        "DELETE FROM tasks; DELETE FROM workflows; DELETE FROM agents;"
    )
    registry._IMPLEMENTATIONS.clear()
    yield db_pool
    registry._IMPLEMENTATIONS.clear()


def response(text, sources=(("Reuters", "https://r.com/a"),)):
    chunks = [
        SimpleNamespace(web=SimpleNamespace(uri=uri, title=title))
        for title, uri in sources
    ]
    return SimpleNamespace(
        text=text,
        candidates=[SimpleNamespace(grounding_metadata=SimpleNamespace(
            grounding_chunks=chunks, web_search_queries=["q"]))],
        usage_metadata=SimpleNamespace(
            prompt_token_count=40, candidates_token_count=20,
            thoughts_token_count=0),
    )


def stub_search(monkeypatch, replies):
    """Stand in for grounded search. `replies` is called with each query.

    Per-query rather than one fixed answer, because the whole capability
    is about checking claims *separately* -- a stub that returned the same
    thing regardless could not tell a real per-claim check from a single
    lookup pretending to be five.
    """
    seen = []

    async def generate_content(*, model, contents, config):
        seen.append(contents)
        out = replies(contents) if callable(replies) else replies
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr(
        websearch.genai, "Client",
        lambda api_key: SimpleNamespace(aio=SimpleNamespace(
            models=SimpleNamespace(generate_content=generate_content))),
    )
    return seen


def stub_splitter(monkeypatch, claims):
    """Stand in for the cheap model that splits prose into claims."""
    import json

    class Fake:
        async def complete(self, message, history=None, memory_context=None):
            Fake.saw = message
            return SimpleNamespace(
                text=json.dumps({"claims": claims}), input_tokens=30,
                output_tokens=10, model="split-test", provider="fake")

    import app.llm

    monkeypatch.setattr(app.llm, "get_provider", lambda settings: Fake())
    return Fake


def handoff(objective="Check these", inputs=None, context_text="", constraints=None,
            permissions=NETWORK):
    return Handoff(
        task_id=None, workflow_id=None, objective=objective, inputs=inputs or {},
        context=context_text, constraints=constraints or {}, permissions=permissions,
    )


# --- the rule that matters most --------------------------------------------

def test_a_verdict_with_no_sources_behind_it_is_not_a_verdict():
    """The exact failure this capability exists to catch.

    A model will happily answer SUPPORTED from its own recollection with
    nothing to cite. Letting that through would turn an unchecked opinion
    into a green tick -- worse than never checking, because it carries a
    stamp.
    """
    assert fc._read_verdict("VERDICT: SUPPORTED\nWHY: everyone knows", []) == fc.UNVERIFIED
    assert fc._read_verdict("VERDICT: CONTRADICTED", []) == fc.UNVERIFIED
    assert fc._read_verdict("VERDICT: SUPPORTED", ["https://r.com/a"]) == fc.SUPPORTED


@pytest.mark.parametrize(
    "text,expected",
    [
        ("VERDICT: SUPPORTED\nWHY: two outlets report it", fc.SUPPORTED),
        ("**VERDICT:** CONTRADICTED\nWHY: the figure was 8%", fc.CONTRADICTED),
        ("verdict - disputed\nwhy: sources differ", fc.DISPUTED),
        ("VERDICT: UNVERIFIED", fc.UNVERIFIED),
        ("The sources contradicted this claim entirely.", fc.CONTRADICTED),
        ("A rambling answer that never commits to anything.", fc.UNVERIFIED),
    ],
)
def test_verdicts_are_read_from_the_answer_or_default_to_unverified(text, expected):
    """Unparseable must fall to unverified, never to a generous guess."""
    assert fc._read_verdict(text, ["https://r.com/a"]) == expected


def test_confidence_is_computed_from_support_not_asserted():
    """Same arithmetic every time, so two results can be compared."""
    four = ["a", "b", "c", "d"]
    assert fc._claim_confidence(fc.SUPPORTED, ["a"]) < fc._claim_confidence(fc.SUPPORTED, four)
    assert fc._claim_confidence(fc.DISPUTED, four) < fc._claim_confidence(fc.SUPPORTED, four)
    assert fc._claim_confidence(fc.UNVERIFIED, []) < fc._claim_confidence(fc.DISPUTED, ["a"])
    assert fc._claim_confidence(fc.SUPPORTED, four * 5) < 1.0, (
        "agreeing sources are still only agreeing sources"
    )
    # A contradiction is a firm finding, not a weak one: knowing a claim is
    # false is as useful as knowing it is true.
    assert fc._claim_confidence(fc.CONTRADICTED, four) == fc._claim_confidence(
        fc.SUPPORTED, four)


# --- the permission boundary -----------------------------------------------

@pytest.mark.asyncio
async def test_checking_is_impossible_without_the_network_permission(monkeypatch):
    called = stub_search(monkeypatch, response("VERDICT: SUPPORTED"))
    monkeypatch.setattr(fc, "get_settings", lambda: SETTINGS)

    with pytest.raises(PermissionDenied, match="network"):
        await websearch.search("q", granted=frozenset({Permission.READ_MEMORY}),
                               settings=SETTINGS)
    assert not called


def test_the_checker_is_not_given_the_owners_memory():
    """Independence is the point.

    A fact-checker shown what the owner already believes has been handed a
    reason to agree with it. Working scope only, and no READ_MEMORY -- if
    this ever changes, it should be a deliberate argued decision rather
    than a line added to a config dict.
    """
    assert Permission.READ_MEMORY not in fc.SPEC.permissions
    assert fc.SPEC.config["scopes"] == ["working"]


# --- checking claims -------------------------------------------------------

@pytest.mark.asyncio
async def test_each_claim_is_checked_separately(monkeypatch):
    """Not one lookup summarising everything -- one lookup per claim."""
    seen = stub_search(monkeypatch, lambda q: response(
        "VERDICT: CONTRADICTED\nWHY: the figure was 8%"
        if "12%" in q else "VERDICT: SUPPORTED\nWHY: two outlets report it"))
    monkeypatch.setattr(fc, "get_settings", lambda: SETTINGS)

    result = await fc.run(handoff(inputs={"claims": [
        "Revenue grew 12% in 2024", "The company is based in Bengaluru"]}), None)

    assert len(seen) == 2, "the claims were not checked independently"
    verdicts = {c["claim"]: c["verdict"] for c in result.output["claims"]}
    assert verdicts["Revenue grew 12% in 2024"] == fc.CONTRADICTED
    assert verdicts["The company is based in Bengaluru"] == fc.SUPPORTED
    assert "do not act on the material" in result.next_action


@pytest.mark.asyncio
async def test_supplied_claims_skip_the_splitting_model(monkeypatch):
    """A caller that already knows what it wants checked shouldn't pay twice."""
    stub_search(monkeypatch, response("VERDICT: SUPPORTED"))
    monkeypatch.setattr(fc, "get_settings", lambda: SETTINGS)
    fake = stub_splitter(monkeypatch, ["should never be used"])
    fake.saw = None

    result = await fc.run(handoff(inputs={"claims": ["A stated claim"]}), None)

    assert fake.saw is None, "the splitter ran even though claims were given"
    assert result.assumptions == ["Claims were supplied by the caller, not extracted"]


@pytest.mark.asyncio
async def test_prose_is_split_into_claims_before_checking(monkeypatch):
    stub_search(monkeypatch, response("VERDICT: SUPPORTED"))
    monkeypatch.setattr(fc, "get_settings", lambda: SETTINGS)
    fake = stub_splitter(monkeypatch, ["Claim one", "Claim two"])

    result = await fc.run(
        handoff(context_text="From research.web: a paragraph of findings."), None)

    assert "a paragraph of findings" in fake.saw
    assert [c["claim"] for c in result.output["claims"]] == ["Claim one", "Claim two"]
    assert result.assumptions == ["Claims split by split-test"]


@pytest.mark.asyncio
async def test_duplicate_claims_are_not_bought_twice(monkeypatch):
    seen = stub_search(monkeypatch, response("VERDICT: SUPPORTED"))
    monkeypatch.setattr(fc, "get_settings", lambda: SETTINGS)

    await fc.run(handoff(inputs={"claims": [
        "Revenue grew 12%.", "revenue grew 12%", "Revenue grew 12%"]}), None)

    assert len(seen) == 1


@pytest.mark.asyncio
async def test_the_number_of_claims_checked_is_capped(monkeypatch):
    """On a free tier the cap is the difference between a check and a 429."""
    seen = stub_search(monkeypatch, response("VERDICT: SUPPORTED"))
    monkeypatch.setattr(fc, "get_settings", lambda: SETTINGS)

    await fc.run(handoff(inputs={"claims": [f"Claim {i}" for i in range(20)]}), None)
    assert len(seen) == fc.DEFAULT_MAX_CLAIMS

    seen.clear()
    await fc.run(handoff(inputs={"claims": [f"Claim {i}" for i in range(20)]},
                         constraints={"max_claims": 2}), None)
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_nothing_checkable_is_reported_rather_than_invented(monkeypatch):
    seen = stub_search(monkeypatch, response("VERDICT: SUPPORTED"))
    monkeypatch.setattr(fc, "get_settings", lambda: SETTINGS)
    stub_splitter(monkeypatch, [])

    result = await fc.run(handoff(context_text="Opinions, all of them."), None)

    assert not seen, "a search was bought with nothing to check"
    assert result.output["claims"] == []
    assert result.confidence == 0.2
    assert result.unresolved


# --- partial failure -------------------------------------------------------

@pytest.mark.asyncio
async def test_one_failed_lookup_does_not_discard_the_others(monkeypatch):
    """Four checks with a gap in them still beat no checks at all."""
    stub_search(monkeypatch, lambda q: (
        RuntimeError("503 backend unavailable") if "second" in q
        else response("VERDICT: SUPPORTED\nWHY: reported widely")))
    monkeypatch.setattr(fc, "get_settings", lambda: SETTINGS)

    result = await fc.run(handoff(inputs={"claims": [
        "The first claim", "The second claim", "The third claim"]}), None)

    by_claim = {c["claim"]: c["verdict"] for c in result.output["claims"]}
    assert by_claim["The first claim"] == fc.SUPPORTED
    assert by_claim["The second claim"] == fc.UNVERIFIED
    assert any("second claim" in u for u in result.unresolved)


@pytest.mark.asyncio
async def test_when_nothing_could_be_checked_the_task_fails(monkeypatch):
    """Five failed lookups are not five honest 'unverified' verdicts.

    Reporting them as verdicts would look like the claims had been
    examined and found wanting. They were never examined at all, and the
    runtime needs to know that so it can retry or record a failure.
    """
    stub_search(monkeypatch, RuntimeError("503 backend unavailable"))
    monkeypatch.setattr(fc, "get_settings", lambda: SETTINGS)

    with pytest.raises(AgentError) as exc:
        await fc.run(handoff(inputs={"claims": ["One", "Two"]}), None)
    assert exc.value.retryable is True


@pytest.mark.asyncio
async def test_a_permanent_failure_is_not_retried(monkeypatch):
    stub_search(monkeypatch, RuntimeError("400 API_KEY_INVALID"))
    monkeypatch.setattr(fc, "get_settings", lambda: SETTINGS)

    with pytest.raises(AgentError) as exc:
        await fc.run(handoff(inputs={"claims": ["One"]}), None)
    assert exc.value.retryable is False


# --- through the foundation ------------------------------------------------

@pytest.mark.asyncio
async def test_research_then_factcheck_runs_as_one_workflow(clean, monkeypatch):
    """The pair, in a chain: what research found is what gets checked.

    This is the point of building factcheck second. Until now a research
    result's confidence came from counting sources; after this it comes
    from something having actually looked.
    """
    stub_search(monkeypatch, lambda q: (
        response("VERDICT: SUPPORTED\nWHY: three outlets agree",
                 sources=(("Reuters", "https://r.com/1"), ("BBC", "https://b.com/2"),
                          ("FT", "https://f.com/3"), ("AP", "https://a.com/4")))
        if "Check the following claim" in q
        else response("India's GDP grew 7.8% in 2024.",
                      sources=(("MoSPI", "https://mospi.gov.in/x"),))))
    monkeypatch.setattr(research_web, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(fc, "get_settings", lambda: SETTINGS)
    stub_splitter(monkeypatch, ["India's GDP grew 7.8% in 2024"])
    await research_web.install()
    await fc.install()

    result = await orchestrator.run(
        "How fast did India's economy grow?", "user:owner",
        steps=[
            orchestrator.Step("research.web", "How fast did India's economy grow?",
                              name="research"),
            orchestrator.Step("factcheck.claims", "Check the research findings",
                              name="check", after=("research",)),
        ],
    )

    assert result["status"] == "completed", result
    rows = {r["capability"]: r for r in await tasks.workflow_tasks(result["workflow_id"])}
    checked = rows["factcheck.claims"]

    assert float(checked["confidence"]) == 0.9
    assert checked["result"]["output"]["claims"][0]["verdict"] == fc.SUPPORTED
    assert len(checked["result"]["evidence"]) == 4
    assert Decimal(checked["spend_inr"]) >= 0

    trace = await telemetry.trace(result["workflow_id"])
    assert "task_completed" in [e["kind"] for e in trace]


@pytest.mark.asyncio
async def test_the_research_answer_reaches_the_checker(clean, monkeypatch):
    """The chain is only worth building if the material actually travels."""
    seen_material = {}

    stub_search(monkeypatch, lambda q: response(
        "VERDICT: SUPPORTED", sources=(("Reuters", "https://r.com/1"),))
        if "Check the following claim" in q
        else response("Tata Motors sold 40,000 EVs last year."))
    monkeypatch.setattr(research_web, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(fc, "get_settings", lambda: SETTINGS)

    import json

    class Fake:
        async def complete(self, message, history=None, memory_context=None):
            seen_material["prompt"] = message
            return SimpleNamespace(
                text=json.dumps({"claims": ["Tata Motors sold 40,000 EVs last year"]}),
                input_tokens=10, output_tokens=5, model="split-test", provider="fake")

    import app.llm

    monkeypatch.setattr(app.llm, "get_provider", lambda settings: Fake())
    await research_web.install()
    await fc.install()

    await orchestrator.run(
        "Tata EV sales", "user:owner",
        steps=[
            orchestrator.Step("research.web", "How many EVs did Tata sell?",
                              name="research"),
            orchestrator.Step("factcheck.claims", "Check the findings",
                              name="check", after=("research",)),
        ],
    )

    assert "40,000 EVs" in seen_material["prompt"], (
        "the checker was asked to check something it was never shown"
    )


@pytest.mark.asyncio
async def test_the_capability_is_refused_if_its_grant_is_narrowed(clean, monkeypatch):
    """Registry-level enforcement, separate from the tool's own check.

    And recorded as a *refusal*, not a failure. A refusal means the system
    worked -- the agent asked for something it may not have. Filing that
    under "failed" alongside a broken search would hide a permission
    mistake in the noise of ordinary breakage.
    """
    called = stub_search(monkeypatch, response("VERDICT: SUPPORTED"))
    monkeypatch.setattr(fc, "get_settings", lambda: SETTINGS)

    narrowed = AgentSpec(
        capability="factcheck.claims", name="Fact check (narrowed)",
        task_types=("factcheck",),
        permissions=frozenset({Permission.READ_MEMORY}),   # no NETWORK
        model_tiers=(ModelTier.STANDARD,), status=Lifecycle.ACTIVE,
        config={"scopes": ["working"]},
    )
    await registry.register(narrowed)
    registry.implement("factcheck.claims", fc.run)

    wf = await tasks.create_workflow("Check something", "user:owner")
    task_id = await tasks.create(
        objective="Check it", capability="factcheck.claims", workflow_id=wf,
        inputs={"claims": ["A claim"]})
    assert await runtime.run_task(task_id) is False

    row = await tasks.get(task_id)
    assert row["status"] == "failed"
    assert "network" in row["failure_reason"].lower()
    assert not called, "the network was reached by an agent without the grant"

    trace = await telemetry.trace(wf)
    assert "task_refused" in [e["kind"] for e in trace], (
        "a permission denial was filed as an ordinary failure"
    )


# --- readability downstream ------------------------------------------------

@pytest.mark.asyncio
async def test_a_structured_result_hands_the_next_task_its_summary(clean):
    """A dict printed into a briefing is noise the reader must decode."""
    wf = await tasks.create_workflow("chain", "user:owner")
    first = await tasks.create(objective="a", capability="factcheck.claims",
                               workflow_id=wf)
    await tasks.complete(first, {"output": {"summary": "Checked 1 claim: 1 supported.",
                                            "claims": [{"claim": "x"}]}},
                         confidence=0.9)
    second = await tasks.create(objective="b", capability="general.writer",
                                workflow_id=wf)
    from app.db import execute

    await execute("UPDATE tasks SET depends_on = $2 WHERE id = $1", second, [first])

    lines = await context._dependency_results(second)
    assert lines == ["From factcheck.claims: Checked 1 claim: 1 supported."]
