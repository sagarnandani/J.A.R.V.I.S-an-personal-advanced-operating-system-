"""Forgetting, and the difference between forgetting and erasing.

The architecture doc specifies the memory system as "store / recall /
correct / forget". Forgetting is not a footnote on that list: an
assistant you cannot take something back from is one you learn to be
careful around, and a personal system nobody speaks freely to is worth
very little.

These tests exist because this is the only part of JARVIS that destroys
data. A bug in recall means a worse answer. A bug here means something is
gone.
"""
import pytest
import pytest_asyncio

from app.memory import (
    delete_memory,
    forget_all,
    forget_memory,
    list_recent,
    purge_forgotten,
    recall_turns,
    restore_memory,
    store_exchange,
    store_memory,
)


@pytest_asyncio.fixture
async def clean_memories(db_pool):
    await db_pool.execute("DELETE FROM memories")
    yield db_pool
    await db_pool.execute("DELETE FROM memories")


# --- forgetting is reversible ---------------------------------------------

@pytest.mark.asyncio
async def test_a_forgotten_memory_is_no_longer_recalled(clean_memories):
    """The whole point: JARVIS stops bringing it up."""
    user_id, _ = await store_exchange("my password hint is my dog", "Noted.")

    assert len(await recall_turns(limit=20, max_chars=8000)) == 2
    assert await forget_memory(user_id) == 2, "the reply must go too"

    recalled = " ".join(t.text for t in await recall_turns(limit=20, max_chars=8000))
    assert "password hint" not in recalled


@pytest.mark.asyncio
async def test_a_forgotten_memory_is_hidden_from_the_list_but_still_there(
    clean_memories,
):
    """Hidden by default, or "forget that" would look like it failed.

    Still findable on request, because forgetting is reversible and an
    undo you cannot see is not much of an undo.
    """
    user_id, _ = await store_exchange("something private", "Noted.")
    await forget_memory(user_id)

    visible = [r["content"] for r in await list_recent(20)]
    everything = [r["content"] for r in await list_recent(20, include_forgotten=True)]

    assert "something private" not in visible
    assert "something private" in everything


@pytest.mark.asyncio
async def test_forgetting_can_be_undone(clean_memories):
    user_id, _ = await store_exchange("my favourite colour is red", "Noted.")
    await forget_memory(user_id)
    assert await restore_memory(user_id) == 2

    recalled = " ".join(t.text for t in await recall_turns(limit=20, max_chars=8000))
    assert "favourite colour is red" in recalled


# --- deleting is not -------------------------------------------------------

@pytest.mark.asyncio
async def test_a_deleted_memory_is_actually_gone(clean_memories):
    """For the things that should never have been written down.

    "Forgotten" is not good enough for a password or something private --
    the row is still sitting in the database. Delete has to really delete.
    """
    user_id, _ = await store_exchange("my bank pin is 1234", "Noted.")

    assert await delete_memory(user_id) == 2

    everything = [r["content"] for r in await list_recent(50, include_forgotten=True)]
    assert "my bank pin is 1234" not in everything


@pytest.mark.asyncio
async def test_forgetting_takes_the_reply_that_quotes_you_with_it(clean_memories):
    """The bug this behaviour exists to prevent.

    Say something private, and JARVIS answers by repeating it back. The
    same words are now stored twice. Forgetting only the half you tapped
    leaves the secret sitting in the other half -- and the owner is told
    it was forgotten, and is wrong.

    This was found by running it for real, not in a unit test: the
    sensitive words survived a purge inside JARVIS's own reply.
    """
    user_id, _ = await store_exchange(
        "my bank pin is 1234", "Noted -- your bank pin is 1234."
    )

    await forget_memory(user_id)

    recalled = " ".join(t.text for t in await recall_turns(limit=20, max_chars=8000))
    assert "1234" not in recalled

    still_stored = " ".join(
        r["content"] for r in await list_recent(50, include_forgotten=True)
    )
    assert still_stored.count("1234") == 2, "forgotten, but not yet erased"


@pytest.mark.asyncio
async def test_deleting_an_exchange_leaves_no_dangling_references(clean_memories):
    """Nothing may point at a row that no longer exists.

    The pair is deleted together, so the usual case is clean. This proves
    it for a memory linked to something outside its own exchange.
    """
    user_id, reply_id = await store_exchange("question", "answer")
    other_id = await store_memory(
        content="unrelated note", category="episodic", origin="stated"
    )
    from app.memory import link_memories

    await link_memories(other_id, user_id)

    await delete_memory(user_id)

    rows = await list_recent(50, include_forgotten=True)
    surviving_ids = {r["id"] for r in rows}
    for row in rows:
        for ref in row["related_memory_ids"]:
            assert ref in surviving_ids, "a reference outlived what it pointed at"


@pytest.mark.asyncio
async def test_acting_on_a_memory_that_does_not_exist_says_so(clean_memories):
    """False, not a silent success.

    Reporting "done" for a memory that was never there would tell the
    owner their private note is gone when it is not.
    """
    from uuid import uuid4

    missing = uuid4()
    assert await forget_memory(missing) == 0
    assert await restore_memory(missing) == 0
    assert await delete_memory(missing) == 0


# --- in bulk ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_forget_all_clears_the_conversation_but_keeps_the_rows(clean_memories):
    await store_exchange("one", "1")
    await store_exchange("two", "2")

    assert await forget_all() == 4
    assert await recall_turns(limit=20, max_chars=8000) == []
    assert len(await list_recent(50, include_forgotten=True)) == 4


@pytest.mark.asyncio
async def test_purge_erases_only_what_was_already_forgotten(clean_memories):
    """Purging must not touch memories that are still in use.

    This is the most destructive operation JARVIS has. If it took
    anything beyond what was already forgotten, the owner would lose
    things they never asked to lose.
    """
    keep_id, _ = await store_exchange("keep this", "ok")
    drop_id, _ = await store_exchange("drop this", "ok")
    await forget_memory(drop_id)

    # Two, not one: forgetting takes the reply with the message.
    assert await purge_forgotten() == 2

    remaining = [r["content"] for r in await list_recent(50, include_forgotten=True)]
    assert "keep this" in remaining
    assert "drop this" not in remaining


@pytest.mark.asyncio
async def test_purging_with_nothing_forgotten_does_nothing(clean_memories):
    await store_exchange("still needed", "ok")
    assert await purge_forgotten() == 0
    assert len(await list_recent(50)) == 2


@pytest.mark.asyncio
async def test_a_scheduled_expiry_is_not_treated_as_forgotten_early(clean_memories):
    """`expires_at` in the future means "still valid until then".

    Forgetting sets it to now. A memory set to expire next week must keep
    working until next week, and must not be swept up by a purge.
    """
    await store_memory(
        content="valid for now", category="episodic", origin="stated"
    )
    await clean_memories.execute(
        "UPDATE memories SET expires_at = now() + interval '7 days'"
    )

    assert len(await list_recent(50)) == 1
    assert await purge_forgotten() == 0
