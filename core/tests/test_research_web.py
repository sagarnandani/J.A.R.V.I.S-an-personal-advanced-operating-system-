"""research.web -- the first capability that touches the real world.

Gemini is stubbed; everything else is real. What is being tested is the
boundary: that reaching the network is impossible without the permission,
that sources survive the round trip, that a failure is classified into
something the runtime can act on, and that all of it is charged and
traced like any other task.
"""
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio

from app.agents import orchestrator, registry, runtime, tasks, telemetry
from app.agents.capabilities import research_web
from app.agents.schemas import (
    AgentSpec,
    Handoff,
    Lifecycle,
    ModelTier,
    Permission,
    PermissionDenied,
)
from app.agents.tools import websearch
from app.config import Settings


@pytest_asyncio.fixture
async def clean(db_pool):
    await db_pool.execute(
        "DELETE FROM agent_metrics; DELETE FROM agent_events; "
        "DELETE FROM tasks; DELETE FROM workflows; DELETE FROM agents;"
    )
    registry._IMPLEMENTATIONS.clear()
    yield db_pool
    registry._IMPLEMENTATIONS.clear()


def fake_response(text="The answer.", sources=(("Reuters", "https://r.com/a"),),
                  queries=("what happened",)):
    chunks = [
        SimpleNamespace(web=SimpleNamespace(uri=uri, title=title))
        for title, uri in sources
    ]
    return SimpleNamespace(
        text=text,
        candidates=[SimpleNamespace(grounding_metadata=SimpleNamespace(
            grounding_chunks=chunks, web_search_queries=list(queries)))],
        usage_metadata=SimpleNamespace(
            prompt_token_count=120, candidates_token_count=80,
            thoughts_token_count=0),
    )


def stub_gemini(monkeypatch, response=None, raises=None):
    """Stand in for the Gemini client, recording what it was asked."""
    seen = {}

    async def generate_content(*, model, contents, config):
        seen["model"] = model
        seen["contents"] = contents
        seen["tools"] = config.tools
        if raises:
            raise raises
        return response or fake_response()

    monkeypatch.setattr(
        websearch.genai, "Client",
        lambda api_key: SimpleNamespace(
            aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
        ),
    )
    return seen


SETTINGS = Settings(gemini_api_key="fake", gemini_model="gemini-test")


# --- the permission boundary ----------------------------------------------

@pytest.mark.asyncio
async def test_the_web_is_unreachable_without_the_network_permission(monkeypatch):
    """Enforced at the tool, not only at the runtime.

    The runtime knows what a task declared; the tool knows what is about
    to happen. A capability that forgets to declare NETWORK must still be
    unable to reach the network, and this is what makes that true.
    """
    called = stub_gemini(monkeypatch)

    with pytest.raises(PermissionDenied, match="network"):
        await websearch.search(
            "anything", granted=frozenset({Permission.READ_MEMORY}), settings=SETTINGS
        )

    assert not called, "the network was reached despite the refusal"


@pytest.mark.asyncio
async def test_holding_the_permission_lets_the_search_run(monkeypatch):
    seen = stub_gemini(monkeypatch)
    result = await websearch.search(
        "what happened today", granted=frozenset({Permission.NETWORK}),
        settings=SETTINGS,
    )
    assert result.answer == "The answer."
    assert seen["tools"], "the search tool was not attached to the request"


# --- evidence --------------------------------------------------------------

@pytest.mark.asyncio
async def test_sources_come_back_with_the_answer(monkeypatch):
    """A research agent whose claims cannot be checked is a confident voice."""
    stub_gemini(monkeypatch, fake_response(sources=(
        ("Reuters", "https://reuters.com/x"), ("BBC", "https://bbc.co.uk/y"),
    )))
    result = await websearch.search(
        "q", granted=frozenset({Permission.NETWORK}), settings=SETTINGS
    )
    assert result.sources == [
        "Reuters — https://reuters.com/x", "BBC — https://bbc.co.uk/y",
    ]
    assert result.queries == ["what happened"]


@pytest.mark.asyncio
async def test_missing_grounding_loses_citations_but_not_the_answer(monkeypatch):
    """Grounding metadata is optional at every level.

    Losing the answer because a field was absent would be bad. Losing the
    citations *silently* would be worse -- an unsupported claim that looks
    verified. So the answer survives and the confidence drops.
    """
    stub_gemini(monkeypatch, SimpleNamespace(
        text="An answer with nothing behind it", candidates=[], usage_metadata=None
    ))
    result = await websearch.search(
        "q", granted=frozenset({Permission.NETWORK}), settings=SETTINGS
    )
    assert result.answer.startswith("An answer")
    assert result.sources == []
    assert research_web._confidence(result) == 0.3


@pytest.mark.asyncio
async def test_confidence_is_computed_from_support_not_asserted():
    """A model rating its own certainty produces a number that tracks nothing."""
    def r(n):
        return websearch.SearchResult(answer="a", sources=[f"s{i}" for i in range(n)])

    assert research_web._confidence(r(0)) < research_web._confidence(r(1))
    assert research_web._confidence(r(1)) < research_web._confidence(r(5))
    assert research_web._confidence(r(50)) < 1.0, (
        "agreement among sources is not the same as being true"
    )


# --- failure classification -----------------------------------------------

@pytest.mark.parametrize(
    "message,retryable",
    [
        ("400 API_KEY_INVALID: key not valid", False),
        ("403 PERMISSION_DENIED", False),
        ("404 NOT_FOUND: model gone", False),
        ("429 RESOURCE_EXHAUSTED: quota", False),
        ("503 backend unavailable", True),
        ("connection reset by peer", True),
        ("something nobody has ever seen", True),
    ],
)
@pytest.mark.asyncio
async def test_failures_are_classified_so_the_runtime_can_act(
    monkeypatch, message, retryable
):
    """A deny-list, for the reason this project learned the hard way.

    Listing what to retry means every unfamiliar failure is treated as
    permanent. Listing the few that are definitely not worth retrying
    means an unknown error costs one retry and still works.
    """
    stub_gemini(monkeypatch, raises=RuntimeError(message))

    with pytest.raises(websearch.SearchUnavailable) as exc:
        await websearch.search(
            "q", granted=frozenset({Permission.NETWORK}), settings=SETTINGS
        )
    assert exc.value.retryable is retryable


@pytest.mark.asyncio
async def test_a_missing_key_is_explained_and_never_retried(monkeypatch):
    stub_gemini(monkeypatch)
    with pytest.raises(websearch.SearchUnavailable, match="GEMINI_API_KEY") as exc:
        await websearch.search(
            "q", granted=frozenset({Permission.NETWORK}),
            settings=Settings(gemini_api_key=None),
        )
    assert exc.value.retryable is False


# --- through the foundation ------------------------------------------------

@pytest.mark.asyncio
async def test_a_real_capability_runs_through_the_whole_foundation(clean, monkeypatch):
    """Registered, routed, permitted, costed and traced like anything else.

    The point of building this capability first: it proves the foundation
    against something that spends money and can genuinely fail, rather
    than against a mock that always cooperates.
    """
    stub_gemini(monkeypatch, fake_response(sources=(
        ("Reuters", "https://reuters.com/x"), ("BBC", "https://bbc.co.uk/y"),
        ("FT", "https://ft.com/z"), ("Guardian", "https://gu.com/w"),
    )))
    monkeypatch.setattr(research_web, "get_settings", lambda: SETTINGS)
    await research_web.install()

    result = await orchestrator.run(
        "What happened in AI today?", "user:owner",
        steps=[orchestrator.Step("research.web", "What happened in AI today?")],
    )

    assert result["status"] == "completed"
    rows = await tasks.workflow_tasks(result["workflow_id"])
    task = rows[0]

    assert float(task["confidence"]) == 0.85
    assert len(task["result"]["evidence"]) == 4, "citations must survive to the task"
    assert Decimal(task["spend_inr"]) >= 0

    trace = await telemetry.trace(result["workflow_id"])
    kinds = [e["kind"] for e in trace]
    assert "agent_selected" in kinds and "task_completed" in kinds


@pytest.mark.asyncio
async def test_the_capability_is_refused_if_its_grant_is_narrowed(clean, monkeypatch):
    """Registry-level enforcement, independent of the tool's own check.

    Registering research.web without NETWORK must stop it, proving the two
    checks are genuinely separate rather than one dressed up as two.
    """
    called = stub_gemini(monkeypatch)
    monkeypatch.setattr(research_web, "get_settings", lambda: SETTINGS)

    narrowed = AgentSpec(
        capability="research.web", name="Web research (narrowed)",
        task_types=("research",),
        permissions=frozenset({Permission.READ_MEMORY}),   # no NETWORK
        model_tiers=(ModelTier.STANDARD,), status=Lifecycle.ACTIVE,
        config={"scopes": ["working"]},
    )
    await registry.register(narrowed)
    registry.implement("research.web", research_web.run)

    wf = await tasks.create_workflow("Research something", "user:owner")
    task_id = await tasks.create(objective="Find out", capability="research.web",
                                 workflow_id=wf)
    assert await runtime.run_task(task_id) is False

    row = await tasks.get(task_id)
    assert row["status"] == "failed"
    assert "network" in row["failure_reason"].lower()
    assert not called, "the network was reached by an agent without the grant"


# --- an answer with nothing behind it must say so --------------------------

@pytest.mark.asyncio
async def test_an_unsourced_answer_says_it_was_never_looked_up(monkeypatch):
    """The failure the owner actually hit.

    Grounding returned nothing, so this is the model's own recollection
    wearing a research agent's name. The confidence already drops to 0.3 --
    but confidence is a number in a task row, and what the owner hears is
    the text. Unlabelled, it sounds exactly like something that was looked
    up, and they were told two-year-old facts as a fresh finding.
    """
    stub_gemini(monkeypatch, SimpleNamespace(
        text="The latest model is from 2024.", candidates=[], usage_metadata=None))
    monkeypatch.setattr(research_web, "get_settings", lambda: SETTINGS)

    result = await research_web.run(
        Handoff(task_id=None, workflow_id=None, objective="what launched recently",
                permissions=frozenset({Permission.NETWORK})),
        None,
    )

    assert "could not reach any sources" in result.output
    assert "may be out of date" in result.output
    assert "The latest model is from 2024." in result.output, (
        "the answer itself was thrown away rather than qualified"
    )
    assert result.confidence == 0.3
    assert result.unresolved


@pytest.mark.asyncio
async def test_a_sourced_answer_is_left_alone(monkeypatch):
    """The caveat must not attach itself to real research."""
    stub_gemini(monkeypatch, fake_response(
        text="The ceiling is Rs.50,000.",
        sources=(("Reuters", "https://r.com/a"), ("BBC", "https://b.com/b"))))
    monkeypatch.setattr(research_web, "get_settings", lambda: SETTINGS)

    result = await research_web.run(
        Handoff(task_id=None, workflow_id=None, objective="the subsidy",
                permissions=frozenset({Permission.NETWORK})),
        None,
    )

    assert result.output == "The ceiling is Rs.50,000."
    assert not result.unresolved
