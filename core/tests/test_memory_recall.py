"""Reading the conversation back out of memory.

This is what makes JARVIS remember. Two things have to hold, and neither
is obvious from reading the happy path:

  * only what was actually said gets replayed -- JARVIS's own guesses
    about the owner must never come back as though they were part of the
    conversation;
  * the history has a hard ceiling, because it is re-sent on every single
    message and would otherwise make each message cost more than the last.
"""
import pytest
import pytest_asyncio

from app.llm.base import ASSISTANT, USER, Turn, normalise_history
from app.memory import recall_turns, store_memory


@pytest_asyncio.fixture
async def clean_memories(db_pool):
    await db_pool.execute("DELETE FROM memories")
    yield db_pool
    await db_pool.execute("DELETE FROM memories")


async def _say(user_text: str, reply_text: str) -> None:
    await store_memory(content=user_text, category="episodic", origin="stated")
    await store_memory(content=reply_text, category="episodic", origin="retrieved")


# --- what comes back ------------------------------------------------------

@pytest.mark.asyncio
async def test_the_conversation_comes_back_in_the_order_it_happened(clean_memories):
    await _say("my favourite colour is red", "Noted.")
    await _say("what is it?", "Red.")

    turns = await recall_turns(limit=20, max_chars=8000)

    assert [(t.role, t.text) for t in turns] == [
        (USER, "my favourite colour is red"),
        (ASSISTANT, "Noted."),
        (USER, "what is it?"),
        (ASSISTANT, "Red."),
    ]


@pytest.mark.asyncio
async def test_only_the_most_recent_turns_are_recalled(clean_memories):
    for i in range(10):
        await _say(f"message {i}", f"reply {i}")

    turns = await recall_turns(limit=4, max_chars=8000)

    assert len(turns) == 4
    assert turns[-1].text == "reply 9"
    assert turns[0].text == "message 8"


@pytest.mark.asyncio
async def test_guesses_about_the_owner_are_never_replayed_as_conversation(
    clean_memories,
):
    """The provenance rule, enforced where it can actually be violated.

    'inferred' and 'predicted' are JARVIS's own conclusions about the
    owner, not things the owner said. Replaying one as conversation would
    hand it back to the model indistinguishable from a real statement --
    and the model would then treat its own guess as established fact. That
    is precisely the blurring the provenance field exists to prevent, so
    it is tested rather than trusted to a comment.
    """
    await _say("I work in mechanical engineering", "Noted.")
    await store_memory(
        content="the owner probably dislikes meetings",
        category="episodic",
        origin="inferred",
        confidence=0.4,
    )
    await store_memory(
        content="the owner will quit their job",
        category="episodic",
        origin="predicted",
        confidence=0.2,
    )

    turns = await recall_turns(limit=20, max_chars=8000)

    recalled = " ".join(t.text for t in turns)
    assert "dislikes meetings" not in recalled
    assert "will quit" not in recalled
    assert "mechanical engineering" in recalled


# --- the budget ceiling ---------------------------------------------------

@pytest.mark.asyncio
async def test_oldest_turns_are_dropped_first_when_over_budget(clean_memories):
    """Trimming has to drop the oldest, not the newest.

    Dropping the newest would mean JARVIS forgets what you just said while
    remembering last week -- which is worse than no memory at all.
    """
    await _say("a" * 400, "b" * 400)
    await _say("the recent one", "the recent reply")

    turns = await recall_turns(limit=20, max_chars=100)

    texts = [t.text for t in turns]
    assert "the recent reply" in texts
    assert "a" * 400 not in texts


@pytest.mark.asyncio
async def test_a_huge_message_from_the_owner_is_trimmed_not_forgotten(clean_memories):
    """One long paste must not wipe out the whole memory.

    Nothing here fits the budget, so the plain trimming loop keeps
    nothing. Returning an empty history would look like total amnesia to
    the owner when it is really a size limit doing its job -- so what they
    said comes back cut down and labelled.
    """
    await _say("please remember this: " + "d" * 5000, "Noted.")

    turns = await recall_turns(limit=20, max_chars=200)

    assert turns, "an oversized message must still produce something"
    assert turns[0].role == USER
    assert turns[0].text.startswith("please remember this:")
    assert "trimmed" in turns[0].text


@pytest.mark.asyncio
async def test_a_huge_reply_leaves_the_owners_words_recalled(clean_memories):
    """The other way round, which used to return nothing at all.

    The newest turn is always JARVIS's reply. If only that fits the
    budget, normalising drops it for starting mid-exchange -- and the
    result was an empty history. What the owner said is the half worth
    keeping anyway.
    """
    await _say("short question", "x" * 5000)

    turns = await recall_turns(limit=20, max_chars=200)

    assert [(t.role, t.text) for t in turns] == [(USER, "short question")]


@pytest.mark.asyncio
async def test_recall_can_be_switched_off_entirely(clean_memories):
    await _say("hello", "hi")
    assert await recall_turns(limit=0, max_chars=8000) == []
    assert await recall_turns(limit=20, max_chars=0) == []


@pytest.mark.asyncio
async def test_no_memories_yet_is_an_empty_conversation_not_an_error(clean_memories):
    assert await recall_turns(limit=20, max_chars=8000) == []


# --- shape the providers require ------------------------------------------

def test_history_that_starts_mid_exchange_is_repaired():
    """Trimming can slice a pair in half and leave a reply on its own.

    Claude rejects a conversation that doesn't start with the user, with
    an error that says nothing about why -- so the shape is fixed once,
    centrally, rather than depending on how the trim happened to land.
    """
    repaired = normalise_history(
        [Turn(ASSISTANT, "a reply to a question we trimmed away"), Turn(USER, "hi")]
    )
    assert [t.role for t in repaired] == [USER]


# --- storing an exchange in one round trip ---------------------------------

@pytest.mark.asyncio
async def test_an_exchange_is_stored_linked_both_ways(clean_memories):
    """The speed optimisation must not lose the link between the pair.

    Storing the message and reply used to be four database calls: two
    inserts and two updates to point them at each other. It is now one
    insert, with both IDs generated up front so each row can name the
    other before either exists. That is only worth doing if the links
    still come out right in both directions.
    """
    from app.memory import store_exchange

    user_id, reply_id = await store_exchange("what is my colour?", "Red.")

    rows = await clean_memories.fetch(
        "SELECT id, content, origin, related_memory_ids FROM memories"
    )
    by_id = {r["id"]: r for r in rows}

    assert by_id[user_id]["origin"] == "stated"
    assert by_id[reply_id]["origin"] == "retrieved"
    assert by_id[user_id]["related_memory_ids"] == [reply_id]
    assert by_id[reply_id]["related_memory_ids"] == [user_id]


@pytest.mark.asyncio
async def test_a_stored_exchange_is_recalled_as_conversation(clean_memories):
    """The fast path and the recall path have to agree.

    Two separate pieces of SQL now write and read the same rows. A change
    to either that broke the other would show up as JARVIS quietly
    forgetting, which is exactly the failure that is hard to notice.
    """
    from app.memory import store_exchange

    await store_exchange("my favourite colour is red", "Noted.")
    turns = await recall_turns(limit=20, max_chars=8000)

    assert [(t.role, t.text) for t in turns] == [
        (USER, "my favourite colour is red"),
        (ASSISTANT, "Noted."),
    ]
