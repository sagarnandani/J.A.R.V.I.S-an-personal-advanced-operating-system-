"""When JARVIS may consult the live web (section 24).

The four modes are easy. The thing worth testing is the one that costs
something: under `required`, a search that comes back empty must FAIL,
because the alternative is an answer made of the model's recollection
wearing a research agent's name. JARVIS labels that today. A label is
something the owner has to read.
"""
from types import SimpleNamespace

import pytest

from app.agents import search_policy
from app.agents.capabilities import research_web
from app.agents.schemas import Handoff, Permission
from app.agents.search_policy import Search
from app.agents.tools import websearch
from app.config import Settings
from tests.test_research_web import SETTINGS, fake_response, stub_gemini

NET = frozenset({Permission.NETWORK, Permission.READ_MEMORY})


def handoff(**constraints) -> Handoff:
    from uuid import uuid4
    return Handoff(
        task_id=uuid4(), workflow_id=uuid4(),
        objective="What is the EV subsidy in Karnataka this month?",
        inputs={}, context="", constraints=constraints,
        expected_output="", budget_inr=None, permissions=NET,
    )


# --- reading a policy ------------------------------------------------------

@pytest.mark.parametrize("written,expected", [
    ("off", Search.OFF), ("OPTIONAL", Search.OPTIONAL),
    (" required ", Search.REQUIRED), ("fallback", Search.FALLBACK),
    (Search.OFF, Search.OFF),
])
def test_a_policy_is_read_however_it_was_written(written, expected):
    assert search_policy.read(written) is expected


def test_a_typo_is_the_default_and_never_off():
    """The failure that would be invisible.

    A mode nobody recognises reading as "off" looks exactly like a task
    that quietly stopped checking its facts -- and it would keep working,
    which is the worst property a bug can have.
    """
    for nonsense in ("of", "none", "no", "disabled", "true", "1"):
        assert search_policy.read(nonsense) is search_policy.DEFAULT
    assert search_policy.DEFAULT is not Search.OFF


def test_nothing_written_is_the_default():
    assert search_policy.read(None) is search_policy.DEFAULT
    assert search_policy.read("") is search_policy.DEFAULT


# --- who wins --------------------------------------------------------------

def test_the_task_beats_the_agent_and_the_agent_beats_the_server():
    spec = SimpleNamespace(config={"search": "fallback"})
    server = Settings(search_policy="off")

    assert search_policy.resolve({"search": "required"}, spec, server) is Search.REQUIRED
    assert search_policy.resolve({}, spec, server) is Search.FALLBACK
    assert search_policy.resolve({}, SimpleNamespace(config={}), server) is Search.OFF


def test_with_nothing_set_anywhere_it_is_the_default():
    assert search_policy.resolve(None, None, None) is search_policy.DEFAULT


def test_every_mode_can_be_explained_to_the_owner():
    for mode in Search:
        said = search_policy.explain(mode)
        assert said and said[0].isupper() and said.endswith(".")


# --- what the modes actually do -------------------------------------------

def test_only_off_stops_a_search():
    assert search_policy.may_search(Search.OFF) is False
    for mode in (Search.OPTIONAL, Search.REQUIRED, Search.FALLBACK):
        assert search_policy.may_search(mode) is True


def test_only_required_turns_a_missing_source_into_a_failure():
    assert search_policy.must_search(Search.REQUIRED) is True
    for mode in (Search.OFF, Search.OPTIONAL, Search.FALLBACK):
        assert search_policy.must_search(mode) is False


# --- research.web honouring it --------------------------------------------

@pytest.mark.asyncio
async def test_off_refuses_instead_of_answering_from_memory(monkeypatch):
    called = stub_gemini(monkeypatch)
    monkeypatch.setattr(research_web, "get_settings", lambda: SETTINGS)

    with pytest.raises(websearch.SearchUnavailable) as raised:
        await research_web.run(handoff(search="off"), SimpleNamespace(model="m"))

    assert not called, "it searched anyway"
    assert raised.value.retryable is False
    assert "switched off" in str(raised.value)


@pytest.mark.asyncio
async def test_required_fails_when_nothing_came_back(monkeypatch):
    """The mode earning its place."""
    stub_gemini(monkeypatch, response=fake_response(
        text="The subsidy is 40%.", sources=(), queries=()))
    monkeypatch.setattr(research_web, "get_settings", lambda: SETTINGS)

    with pytest.raises(websearch.SearchUnavailable) as raised:
        await research_web.run(handoff(search="required"), SimpleNamespace(model="m"))
    assert "must be answered from sources" in str(raised.value)


@pytest.mark.asyncio
async def test_optional_still_answers_but_says_what_it_is(monkeypatch):
    """The same empty search, one mode over. Answered, and labelled."""
    stub_gemini(monkeypatch, response=fake_response(
        text="The subsidy is 40%.", sources=(), queries=()))
    monkeypatch.setattr(research_web, "get_settings", lambda: SETTINGS)

    result = await research_web.run(handoff(search="optional"), SimpleNamespace(model="m"))
    assert "40%" in result.output
    assert "has been checked" in result.output or "not been checked" in result.output
    assert result.unresolved, "an unsourced answer went out with nothing said"
    assert result.confidence <= 0.3


@pytest.mark.asyncio
async def test_required_is_satisfied_when_sources_do_come_back(monkeypatch):
    stub_gemini(monkeypatch)
    monkeypatch.setattr(research_web, "get_settings", lambda: SETTINGS)

    result = await research_web.run(handoff(search="required"), SimpleNamespace(model="m"))
    assert result.evidence == ["Reuters — https://r.com/a"]
    assert result.unresolved == []


@pytest.mark.asyncio
async def test_the_policy_in_force_is_on_the_record(monkeypatch):
    """"Why did it refuse" needs an answer in the task row, not in a log."""
    stub_gemini(monkeypatch)
    monkeypatch.setattr(research_web, "get_settings", lambda: SETTINGS)

    result = await research_web.run(handoff(search="fallback"), SimpleNamespace(model="m"))
    assert "Search policy: fallback" in result.assumptions
