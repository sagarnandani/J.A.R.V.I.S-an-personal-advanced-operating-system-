"""Long-term memory: keeping what matters beyond the recent chat.

Conversation recall reaches back about ten exchanges. Everything here
exists because that is a chat window, not a memory -- tell JARVIS a
birthday, talk about something else for ten minutes, and it was gone.
"""
import pytest
import pytest_asyncio

from app.facts import (
    FACT_ORIGIN,
    _parse_reply,
    learn_from_exchange,
    list_facts,
    recall_facts,
)
from app.llm.base import LLMResult
from app.memory import store_exchange, store_memory


class FakeExtractor:
    """Stands in for the model doing the extraction."""

    def __init__(self, reply: str):
        self.reply = reply
        self.prompts = []

    async def complete(self, message, history=None, memory_context=None):
        self.prompts.append(message)
        return LLMResult(
            text=self.reply, input_tokens=1, output_tokens=1, model="f", provider="f"
        )


@pytest_asyncio.fixture
async def clean(db_pool):
    await db_pool.execute("DELETE FROM memories")
    yield db_pool
    await db_pool.execute("DELETE FROM memories")


async def _fact(text: str, category: str = "semantic"):
    return await store_memory(
        content=text, category=category, origin=FACT_ORIGIN, confidence=0.8
    )


# --- reading the model's answer -------------------------------------------
#
# Never allowed to raise. The owner already has their reply by the time
# this runs; a model returning something odd must cost a log line, not
# the conversation.

@pytest.mark.parametrize(
    "raw",
    [
        '{"facts": [{"text": "Owner likes red", "category": "preference"}], "supersedes": []}',
        '```json\n{"facts": [{"text": "Owner likes red"}], "supersedes": []}\n```',
        'Here is the JSON:\n{"facts": [{"text": "Owner likes red"}], "supersedes": []}',
    ],
)
def test_json_is_read_however_the_model_wraps_it(raw):
    got, _ = _parse_reply(raw)
    assert got[0]["text"] == "Owner likes red"


@pytest.mark.parametrize(
    "raw",
    ["not json at all", "", "{broken", "[]", "null", '{"facts": "not a list"}'],
)
def test_unparseable_answers_mean_nothing_learned_not_a_crash(raw):
    assert _parse_reply(raw) == ([], [])


# --- learning --------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_durable_fact_is_written_down(clean):
    source_id, _ = await store_exchange("my wife's birthday is 3 March", "Noted.")
    provider = FakeExtractor(
        '{"facts": [{"text": "Owner wife birthday is 3 March", '
        '"category": "people"}], "supersedes": []}'
    )

    learned, retired = await learn_from_exchange(
        provider, "my wife's birthday is 3 March", "Noted.", source_id, 5
    )

    assert (learned, retired) == (1, 0)
    stored = await list_facts()
    assert stored[0]["content"] == "Owner wife birthday is 3 March"
    assert stored[0]["category"] == "people"


@pytest.mark.asyncio
async def test_a_fact_points_back_at_the_words_it_came_from(clean):
    """The provenance chain must not break.

    A fact is JARVIS's own sentence, so it is stored as `inferred` rather
    than `stated`. That is only honest if you can still get from the
    summary back to what was actually said.
    """
    source_id, _ = await store_exchange("I work in mechanical engineering", "Noted.")
    provider = FakeExtractor(
        '{"facts": [{"text": "Owner is a mechanical engineer"}], "supersedes": []}'
    )

    await learn_from_exchange(provider, "...", "...", source_id, 5)

    fact = (await list_facts())[0]
    assert fact["origin"] == FACT_ORIGIN, "a summary is not something the owner said"
    assert source_id in fact["related_memory_ids"]
    assert float(fact["confidence"]) < 1.0


@pytest.mark.asyncio
async def test_an_invented_id_cannot_retire_a_real_fact(clean):
    """A model asked for ids will sometimes invent one.

    Acting on an invented id would retire a memory nothing was ever said
    against -- the owner losing something they never corrected.
    """
    await _fact("Owner's favourite colour is red")
    from uuid import uuid4

    provider = FakeExtractor(
        '{"facts": [], "supersedes": ["%s", "not-even-a-uuid"]}' % uuid4()
    )

    _, retired = await learn_from_exchange(provider, "hi", "hello", None, 5)

    assert retired == 0
    assert len(await list_facts()) == 1


@pytest.mark.asyncio
async def test_correcting_yourself_retires_the_old_fact(clean):
    """This is "correct", and it is why extraction sees existing facts.

    Without it, "actually it's blue" leaves two contradictory facts side
    by side and JARVIS has no way to tell which one is current.
    """
    old_id = await _fact("Owner's favourite colour is red")
    provider = FakeExtractor(
        '{"facts": [{"text": "Owner favourite colour is blue"}], '
        '"supersedes": ["%s"]}' % old_id
    )

    learned, retired = await learn_from_exchange(
        provider, "actually it's blue", "Noted.", None, 5
    )

    assert (learned, retired) == (1, 1)
    current = [r["content"] for r in await list_facts()]
    assert "Owner favourite colour is blue" in current
    assert "Owner favourite colour is red" not in current
    # Retired, not destroyed -- it is still there to restore or inspect.
    assert len(await list_facts(include_retired=True)) == 2


@pytest.mark.asyncio
async def test_the_model_is_shown_what_is_already_known(clean):
    """Otherwise it cannot spot a contradiction or avoid a duplicate."""
    await _fact("Owner's favourite colour is red")
    provider = FakeExtractor('{"facts": [], "supersedes": []}')

    await learn_from_exchange(provider, "hi", "hello", None, 5)

    assert "Owner's favourite colour is red" in provider.prompts[0]


@pytest.mark.asyncio
async def test_a_flood_of_facts_from_one_exchange_is_capped(clean):
    """One message should not be able to fill memory by itself."""
    many = ", ".join('{"text": "fact %d"}' % i for i in range(20))
    provider = FakeExtractor('{"facts": [%s], "supersedes": []}' % many)

    learned, _ = await learn_from_exchange(provider, "hi", "hello", None, 5)

    assert learned == 5


# --- recalling -------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_relevant_fact_comes_back_however_old_it_is(clean):
    """The whole point.

    In the old behaviour this fact would have scrolled out of the recent
    window and been lost. It is found by what the message is ABOUT, not
    by when it was said.
    """
    await _fact("Owner's wife's birthday is 3 March")
    for i in range(30):
        await _fact(f"Owner mentioned unrelated thing {i}")

    recalled = await recall_facts("when is my wife's birthday?", 5, 2000)

    assert any("wife" in f for f in recalled)


@pytest.mark.asyncio
async def test_retired_facts_are_never_recalled(clean):
    old_id = await _fact("Owner's favourite colour is red")
    await _fact("Owner's favourite colour is blue")
    provider = FakeExtractor('{"facts": [], "supersedes": ["%s"]}' % old_id)
    await learn_from_exchange(provider, "x", "y", None, 5)

    recalled = " ".join(await recall_facts("what colour do I like?", 10, 2000))

    assert "blue" in recalled
    assert "red" not in recalled


@pytest.mark.asyncio
async def test_facts_are_capped_by_character_budget(clean):
    for i in range(20):
        await _fact("x" * 100 + str(i))

    recalled = await recall_facts("anything", limit=20, max_chars=250)

    assert sum(len(f) for f in recalled) <= 250


@pytest.mark.asyncio
async def test_recall_can_be_switched_off(clean):
    await _fact("Owner likes red")
    assert await recall_facts("colour", 0, 2000) == []
    assert await recall_facts("colour", 10, 0) == []


@pytest.mark.asyncio
async def test_no_facts_yet_is_empty_not_an_error(clean):
    assert await recall_facts("anything at all", 10, 2000) == []


@pytest.mark.asyncio
async def test_the_same_fact_is_never_stored_twice(clean):
    """Duplicates degrade memory quietly.

    The prompt asks the model not to repeat what it already knows, and a
    good one mostly obliges. Mostly is not enough: each duplicate eats the
    fact budget and crowds out something JARVIS would otherwise recall, so
    memory looks full while holding one thing many times. Found by running
    the real flow, where a fact landed thirteen times.
    """
    provider = FakeExtractor(
        '{"facts": [{"text": "Owner wife birthday is 3 March"}], "supersedes": []}'
    )

    for _ in range(5):
        await learn_from_exchange(provider, "x", "y", None, 5)

    assert len(await list_facts()) == 1


@pytest.mark.asyncio
async def test_duplicates_within_one_reply_are_also_collapsed(clean):
    provider = FakeExtractor(
        '{"facts": [{"text": "Owner likes red"}, {"text": "owner  likes   RED"}], '
        '"supersedes": []}'
    )

    learned, _ = await learn_from_exchange(provider, "x", "y", None, 5)

    assert learned == 1
