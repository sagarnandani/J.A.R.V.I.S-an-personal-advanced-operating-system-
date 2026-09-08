"""Noticing that a message wanted doing, not answering.

The chat box answered from what the model held; the agents could read the
live web. Same question, two different answers, and nothing said which one
you were getting. These cover the join.

Most of the weight is on the marker never reaching the owner. An offer
that is missed costs nothing; "[[JARVIS_CAN_DO: ...]]" hanging off the end
of a reply is JARVIS visibly leaking its own machinery, and it would be
stored in memory that way too.
"""
from types import SimpleNamespace

import pytest
import pytest_asyncio

from app import offer
from app.agents import builtin, registry, tasks
from app.agents.capabilities import factcheck_claims, research_web


@pytest_asyncio.fixture
async def clean(db_pool):
    await db_pool.execute(
        "DELETE FROM agent_metrics; DELETE FROM agent_events; "
        "DELETE FROM tasks; DELETE FROM workflows; DELETE FROM agents; "
        "DELETE FROM memories;"
    )
    registry._IMPLEMENTATIONS.clear()
    yield db_pool
    registry._IMPLEMENTATIONS.clear()


# --- the marker must never reach the owner --------------------------------

@pytest.mark.parametrize("reply", [
    "The rate was 8%.\n\n[[JARVIS_CAN_DO: check the current rate]]",
    "The rate was 8%. [[JARVIS_CAN_DO: check the current rate]]",
    "The rate was 8%.\n[[ JARVIS_CAN_DO : check the current rate ]]",
    "The rate was 8%.\n[[jarvis_can_do: check the current rate]]",
    "The rate was 8%.\n[JARVIS_CAN_DO: check the current rate]",
    "The rate was 8%.\n[[JARVIS_CAN_DO check the current rate]]",
])
def test_a_well_formed_marker_is_taken_off_and_understood(reply):
    clean_reply, objective = offer.split(reply)
    assert "JARVIS_CAN_DO" not in clean_reply.upper()
    assert clean_reply == "The rate was 8%."
    assert objective == "check the current rate"


@pytest.mark.parametrize("reply", [
    "The rate was 8%.\n[[JARVIS_CAN_DO: check the current rate",
    "The rate was 8%.\n[[JARVIS_CAN_DO",
    "The rate was 8%.\nJARVIS_CAN_DO: check the current rate]]",
    "The rate was 8%.\n[[JARVIS_CAN_DO:]]",
])
def test_a_broken_marker_is_still_taken_off(reply):
    """Erring towards removing more.

    A reply that reaches the owner with the machinery hanging off it is a
    worse failure than one that quietly misses an offer, so every path
    strips -- including the ones that cannot be understood.
    """
    clean_reply, _ = offer.split(reply)
    assert "JARVIS_CAN_DO" not in clean_reply.upper()
    assert clean_reply.startswith("The rate was 8%.")


def test_a_marker_in_the_middle_does_not_eat_the_reply():
    clean_reply, objective = offer.split(
        "First part. [[JARVIS_CAN_DO: go and check it]] Second part.")
    assert "JARVIS_CAN_DO" not in clean_reply.upper()
    assert "First part." in clean_reply and "Second part." in clean_reply
    assert objective == "go and check it"


def test_an_empty_objective_is_not_an_offer():
    """A marker with nothing in it is machinery misfiring, not a proposal.

    Falling back to the owner's own message would be guessing at what they
    meant and spending their money on the guess.
    """
    _, objective = offer.split("Answer.\n[[JARVIS_CAN_DO: go]]")
    assert objective is None


def test_an_ordinary_reply_is_returned_untouched():
    text = "Welcome back, sir. Sneha's birthday is on 3 March."
    assert offer.split(text) == (text, None)


def test_splitting_never_raises():
    for odd in ["", "   ", "[[", "]]", "[[JARVIS_CAN_DO"]:
        clean_reply, objective = offer.split(odd)
        assert isinstance(clean_reply, str)
        assert objective is None or isinstance(objective, str)


# --- an offer must be one JARVIS can honour -------------------------------

@pytest.mark.asyncio
async def test_no_offer_when_nothing_is_registered(clean):
    """The prompt always invites the marker; the registry decides.

    If the agents failed to install, JARVIS must not promise work it
    cannot do -- so the mark is dropped rather than shown.
    """
    assert await offer.can_act() is False
    assert await offer.build("check the current rate") is None


@pytest.mark.asyncio
async def test_an_offer_is_made_when_something_can_take_it(clean):
    await builtin.install()
    await research_web.install()
    await factcheck_claims.install()

    proposal = await offer.build("check the current EV subsidy")
    assert proposal["objective"] == "check the current EV subsidy"
    assert proposal["cost_note"]


@pytest.mark.asyncio
async def test_a_writer_alone_is_not_something_to_offer(clean):
    """general.writer takes "general" work but reaches nothing outside.

    Offering to "look this up properly" when the only agent available
    rewrites prose would be a promise of new information that no new
    information could come from.
    """
    from app.agents.schemas import AgentSpec, Lifecycle, ModelTier, Permission

    await registry.register(AgentSpec(
        capability="general.writer", name="Writer", task_types=("general",),
        permissions=frozenset({Permission.READ_MEMORY}),
        model_tiers=(ModelTier.CHEAP,), status=Lifecycle.ACTIVE))

    assert await offer.can_act() is False


# --- what it costs is measured or absent ----------------------------------

@pytest.mark.asyncio
async def test_the_first_offer_admits_it_has_no_measurement(clean):
    await builtin.install()
    proposal = await offer.build("find something out")

    assert proposal["typical_cost_inr"] is None
    assert "no measurement yet" in proposal["cost_note"]
    assert "Rs." not in proposal["cost_note"], (
        "a figure was produced for a run nothing comparable has ever cost"
    )


@pytest.mark.asyncio
async def test_later_offers_quote_what_runs_have_actually_cost(clean):
    await builtin.install()
    for spend in ("1.00", "2.00", "9.00"):
        wf = await tasks.create_workflow("past work", "user:owner")
        task_id = await tasks.create(objective="x", capability="general.research",
                                     workflow_id=wf)
        await clean.execute("UPDATE tasks SET spend_inr = $2 WHERE id = $1",
                            task_id, __import__("decimal").Decimal(spend))
        await tasks.set_workflow_status(wf, "completed")

    proposal = await offer.build("find something out")
    # The median, not the mean: one expensive run must not make every
    # future offer look dear.
    assert proposal["typical_cost_inr"] == 2.00
    assert "Rs.2.00" in proposal["cost_note"]


# --- what the work found is remembered ------------------------------------

@pytest.mark.asyncio
async def test_what_the_work_found_becomes_memory(clean):
    """Otherwise a research run answers the question and is forgotten.

    That is the exact failure long-term memory exists to stop, and it
    would be invisible: the answer looks right today.
    """
    await offer.remember_outcome(
        "check the EV subsidy", "The ceiling is Rs.50,000, per two sources.")

    rows = await clean.fetch(
        "SELECT content, origin, category FROM memories ORDER BY created_at DESC")
    assert len(rows) == 1
    assert "check the EV subsidy" in rows[0]["content"]
    assert "Rs.50,000" in rows[0]["content"]
    # 'retrieved': material that came back from outside. Not 'stated' (the
    # owner did not say it) and not 'inferred' (JARVIS did not conclude it).
    assert rows[0]["origin"] == "retrieved"


@pytest.mark.asyncio
async def test_remembering_nothing_stores_nothing(clean):
    await offer.remember_outcome("an objective", "   ")
    assert await clean.fetchval("SELECT count(*) FROM memories") == 0


@pytest.mark.asyncio
async def test_a_failure_to_remember_never_breaks_the_work(clean, monkeypatch):
    async def boom(**kwargs):
        raise RuntimeError("database blinked")

    monkeypatch.setattr("app.memory.store_memory", boom)
    await offer.remember_outcome("an objective", "a finding")   # must not raise


# --- the instruction the model is given -----------------------------------

def test_the_prompt_tells_the_model_what_not_to_use_it_for():
    """Half of this instruction is restraint.

    A model that marks every message spends the owner's money on nothing,
    which is worse than never offering at all.
    """
    # Whitespace-collapsed: the instruction is wrapped prose, so asserting
    # on it line-by-line would fail whenever a sentence moved a word.
    import re

    text = re.sub(r"\s+", " ", offer.INSTRUCTION.lower())
    assert "current information" in text
    assert "most messages need no marker" in text
    assert "the owner never sees it" in text


def test_the_instruction_reaches_the_model():
    from app.llm.base import JARVIS_SYSTEM_PROMPT

    assert offer.MARKER in JARVIS_SYSTEM_PROMPT


# --- through the real message route ---------------------------------------

@pytest_asyncio.fixture
async def chat(clean, monkeypatch):
    """The real /v1/message route, with the model stubbed.

    Driven in this test's own event loop rather than through TestClient,
    which runs the app on a loop of its own and cannot share this one's
    database pool.
    """
    import httpx
    from fastapi import FastAPI

    from app.auth import get_current_user
    from app.config import Settings, get_settings
    from app.routes import message as message_route

    settings = Settings(llm_provider="mock", memory_facts_enabled=False,
                        memory_recall_enabled=False, session_secret="x" * 32)

    app = FastAPI()
    app.include_router(message_route.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        uid="owner", email="owner@example.com")
    app.dependency_overrides[get_settings] = lambda: settings

    def reply_with(text: str) -> None:
        class Fake:
            async def complete(self, message, history=None, memory_context=None):
                return SimpleNamespace(text=text, input_tokens=10, output_tokens=5,
                                       model="test", provider="mock")

        monkeypatch.setattr(message_route, "get_provider", lambda s: Fake())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield SimpleNamespace(client=client, reply_with=reply_with)


@pytest.mark.asyncio
async def test_the_owner_never_sees_the_marker(chat, clean):
    await builtin.install()
    await research_web.install()
    chat.reply_with("My figure may be stale.\n\n[[JARVIS_CAN_DO: check the EV subsidy]]")

    body = (await chat.client.post("/v1/message", json={"text": "EV subsidy?"})).json()

    assert body["reply"] == "My figure may be stale."
    assert "JARVIS_CAN_DO" not in body["reply"]
    assert body["offer"]["objective"] == "check the EV subsidy"


@pytest.mark.asyncio
async def test_the_marker_is_never_written_into_memory(chat, clean):
    """The reply is stored as JARVIS's own words.

    Storing the marker would put the machinery into the conversation
    history for good, where it would be replayed into every later prompt
    and read back to the owner on recall.
    """
    await builtin.install()
    await research_web.install()
    chat.reply_with("Answer.\n[[JARVIS_CAN_DO: go and check it properly]]")

    await chat.client.post("/v1/message", json={"text": "check something"})

    stored = await clean.fetch("SELECT content FROM memories")
    assert stored, "the exchange was not recorded at all"
    for row in stored:
        assert "JARVIS_CAN_DO" not in row["content"]


@pytest.mark.asyncio
async def test_an_ordinary_message_carries_no_offer(chat, clean):
    """Chat stays chat. Most messages are not jobs."""
    await builtin.install()
    await research_web.install()
    chat.reply_with("Welcome back, sir.")

    body = (await chat.client.post("/v1/message", json={"text": "daddy's home"})).json()
    assert body["reply"] == "Welcome back, sir."
    assert body["offer"] is None


@pytest.mark.asyncio
async def test_a_marked_message_still_answers_when_nothing_can_act(chat, clean):
    """The registry is empty, so there is no offer -- but there is a reply.

    Dropping the answer because the offer could not be honoured would
    turn a missing extra into a broken conversation.
    """
    chat.reply_with("Best I have is 8%.\n[[JARVIS_CAN_DO: check the real figure]]")

    body = (await chat.client.post("/v1/message", json={"text": "rate?"})).json()
    assert body["reply"] == "Best I have is 8%."
    assert body["offer"] is None


# --- the spoken path ------------------------------------------------------

def test_a_speaking_model_is_never_told_to_emit_the_marker():
    """The regression this file exists to stop happening twice.

    The marker is written text: invisible in a typed reply, stripped
    before anyone sees it. A model generating speech would read the
    brackets out loud -- "bracket bracket JARVIS underscore CAN underscore
    DO" -- on every reply that wanted looking up.
    """
    from app.llm.base import system_prompt_with

    assert offer.MARKER in system_prompt_with(None)
    assert offer.MARKER not in system_prompt_with(None, offers=False)
    assert offer.MARKER not in system_prompt_with("some notes", offers=False)


def test_the_spoken_prompt_still_carries_the_persona_and_the_notes():
    """Dropping the marker must not drop everything else with it."""
    from app.llm.base import system_prompt_with

    spoken = system_prompt_with("Owner's wife is called Sneha.", offers=False)
    assert "Address him as 'sir'" in spoken
    assert "Sneha" in spoken


def test_voice_asks_for_the_prompt_without_the_marker():
    """Checked at the call site, not only in the helper.

    A correct helper called with the wrong argument is the same bug, and
    this is the line that had it wrong.
    """
    import inspect

    from app.routes import live

    source = inspect.getsource(live)
    assert "system_prompt_with(context, offers=False)" in source


@pytest.mark.parametrize("said,expected", [
    ("what is the latest on the EV policy", True),
    ("can you check the karnataka subsidy for me", True),
    ("haan check karo the latest news", True),
    ("is it true that the subsidy went up", True),
    ("good morning", False),
    ("what is my wife's name", False),
    ("yes", False),
    ("", False),
])
def test_the_free_filter_keeps_most_turns_from_costing_anything(said, expected):
    """A model call per spoken turn is real money on a free quota.

    The filter is deliberately loose: a false positive costs one cheap
    call, a false negative costs an offer nobody was promised.
    """
    assert offer.looks_like_a_request(said) is expected


@pytest.mark.asyncio
async def test_a_spoken_request_becomes_an_objective(clean):
    await builtin.install()
    await research_web.install()

    class Fake:
        async def complete(self, message, history=None, memory_context=None):
            assert "EV policy" in message, "the model was not shown what was said"
            return SimpleNamespace(
                text='{"needed": true, "objective": "find the current Karnataka EV subsidy"}',
                input_tokens=1, output_tokens=1, model="t", provider="mock")

    got = await offer.from_speech("what is the latest on the EV policy", Fake())
    assert got == "find the current Karnataka EV subsidy"


@pytest.mark.asyncio
async def test_chat_that_only_sounds_like_a_request_is_dropped(clean):
    """The filter lets things through; the model is what decides."""
    await builtin.install()

    class Fake:
        async def complete(self, message, history=None, memory_context=None):
            return SimpleNamespace(text='{"needed": false}', input_tokens=1,
                                   output_tokens=1, model="t", provider="mock")

    assert await offer.from_speech("check with me later today", Fake()) is None


@pytest.mark.asyncio
async def test_the_filter_runs_before_the_model_is_ever_called(clean):
    await builtin.install()
    called = False

    class Fake:
        async def complete(self, *a, **k):
            nonlocal called
            called = True
            raise AssertionError("a model was called on ordinary chat")

    assert await offer.from_speech("good morning", Fake()) is None
    assert called is False


@pytest.mark.asyncio
async def test_a_bad_reply_costs_a_missed_offer_and_nothing_else(clean):
    """This runs while the owner is mid-conversation."""
    await builtin.install()

    class Junk:
        async def complete(self, *a, **k):
            return SimpleNamespace(text="sorry, what?", input_tokens=1,
                                   output_tokens=1, model="t", provider="mock")

    class Broken:
        async def complete(self, *a, **k):
            raise RuntimeError("503")

    assert await offer.from_speech("check the latest news", Junk()) is None
    assert await offer.from_speech("check the latest news", Broken()) is None


# --- answering out loud ---------------------------------------------------

@pytest.mark.parametrize("said", [
    "yes go ahead", "yes", "go ahead", "sure", "please do", "ok do it",
    "haan karo", "yeah go for it", "absolutely", "proceed",
])
def test_agreeing_out_loud_is_understood(said):
    """The bug this exists to stop: a question asked in speech that could
    only be answered by a button. Said aloud, "yes, go ahead" reached
    nothing at all, so JARVIS never learned it had offered and asked
    again -- and again."""
    assert offer.reads_as_yes(said) is True
    assert offer.reads_as_no(said) is False


@pytest.mark.parametrize("said", [
    "no", "no thanks", "not now", "leave it", "never mind", "nahi", "cancel",
])
def test_declining_out_loud_is_understood(said):
    assert offer.reads_as_no(said) is True
    assert offer.reads_as_yes(said) is False


@pytest.mark.parametrize("said", [
    "what is the weather like",
    "tell me yes or no whether that is right",
    "the answer is probably yes but check it",
    "",
])
def test_anything_else_is_neither(said):
    """An unrecognised answer must leave the offer standing.

    Guessing either way is worse than waiting: guessing yes spends money
    on a question nobody answered, guessing no drops something the owner
    may still be thinking about.
    """
    assert offer.reads_as_yes(said) is False
    assert offer.reads_as_no(said) is False


def test_only_the_opening_words_count():
    """A "yes" buried mid-sentence is not an agreement.

    "Tell me yes or no about this" would otherwise start work nobody
    asked for, and the owner would hear a search beginning in the middle
    of an unrelated question.
    """
    assert offer.reads_as_yes("i said yes earlier but not to that") is False
    assert offer.reads_as_yes("yes, and while you are at it check the date") is True
