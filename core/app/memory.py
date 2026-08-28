"""Memory store, with provenance kept explicit at every write.

The single rule this module exists to enforce (architecture doc, section
E): a memory's `origin` must never blur what the user actually said with
what JARVIS inferred or predicted about them. Stage 0 does no
interpretation at all, so every write here is either 'stated' (the user's
own words) or 'retrieved' (content that came back from the model/an
external source, asserted as nothing more than that). 'inferred' and
'predicted' are valid values in the schema starting now, but nothing in
Stage 0's code path produces them -- they wait for a real interpretation
layer in a later stage.
"""
from uuid import UUID, uuid4

from app.db import execute, fetchrow
from app.llm.base import ASSISTANT, USER, Turn, normalise_history

VALID_CATEGORIES = {
    "working", "episodic", "semantic", "project",
    "people", "decision", "task", "preference", "system",
}
VALID_ORIGINS = {"stated", "retrieved", "inferred", "predicted"}


async def store_memory(
    content: str,
    category: str,
    origin: str,
    confidence: float = 1.0,
    related_memory_ids: list[UUID] | None = None,
) -> UUID:
    if category not in VALID_CATEGORIES:
        raise ValueError(f"Unknown memory category: {category!r}")
    if origin not in VALID_ORIGINS:
        raise ValueError(f"Unknown memory origin: {origin!r}")
    if not 0.0 <= confidence <= 1.0:
        raise ValueError(f"confidence must be between 0 and 1, got {confidence!r}")

    row = await fetchrow(
        """
        INSERT INTO memories (content, category, origin, confidence, related_memory_ids)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id
        """,
        content,
        category,
        origin,
        confidence,
        related_memory_ids or [],
    )
    assert row is not None
    return row["id"]


async def store_exchange(user_text: str, reply_text: str) -> tuple[UUID, UUID]:
    """Store a message and its reply, linked, in one round trip.

    The obvious version of this is four separate database calls: insert
    the message, insert the reply, then two updates to point them at each
    other. Locally that is free. Against a hosted database it is four
    network round trips, every one of them after JARVIS has already
    worked out its answer -- so the owner sits watching a spinner while
    the machine does bookkeeping.

    The trick is generating both IDs here rather than letting Postgres do
    it. Once each row knows the other's ID before either is written, both
    can be inserted with their links already set, and no update is needed
    at all.

    (A data-modifying CTE cannot replace this: rows inserted by a CTE are
    not visible to an UPDATE in the same statement, so the update would
    silently match nothing.)
    """
    user_id = uuid4()
    reply_id = uuid4()

    # The timestamps are set explicitly, one millisecond apart, and that
    # is not a detail. Both rows are written by a single statement, and
    # inside one statement now() returns the SAME instant for every row --
    # so left to the column default the message and the reply would carry
    # identical timestamps, and "order by time" could put the reply first.
    # Recall then drops it for starting mid-exchange, and JARVIS quietly
    # remembers only half of every conversation.
    #
    # The reply genuinely did come after the message, so recording it that
    # way is accurate, not a trick.
    await execute(
        """
        INSERT INTO memories
            (id, content, category, origin, confidence, related_memory_ids,
             created_at)
        VALUES
            ($1, $2, 'episodic', 'stated',    1.0, ARRAY[$3]::uuid[], now()),
            ($3, $4, 'episodic', 'retrieved', 1.0, ARRAY[$1]::uuid[],
             now() + interval '1 millisecond')
        """,
        user_id,
        user_text,
        reply_id,
        reply_text,
    )
    return user_id, reply_id


async def link_memories(a: UUID, b: UUID) -> None:
    """Record that two memories relate to each other (e.g. a message and its reply)."""
    await execute(
        "UPDATE memories SET related_memory_ids = array_append(related_memory_ids, $2) WHERE id = $1",
        a,
        b,
    )
    await execute(
        "UPDATE memories SET related_memory_ids = array_append(related_memory_ids, $2) WHERE id = $1",
        b,
        a,
    )


async def list_recent(limit: int = 20) -> list:
    from app.db import fetch

    return await fetch(
        "SELECT * FROM memories ORDER BY created_at DESC LIMIT $1", limit
    )


# Which origins can be replayed as conversation, and as whose words.
#
# Only these two. 'inferred' and 'predicted' are JARVIS's own guesses
# about the owner, and feeding a guess back in as though it were part of
# the conversation is how a guess quietly becomes a fact -- the exact
# blurring the provenance rule exists to prevent. When a later stage
# starts producing them, they will need their own path in, clearly marked
# as guesses. They must not silently arrive through this one.
_ORIGIN_ROLES = {"stated": USER, "retrieved": ASSISTANT}


async def recall_turns(limit: int, max_chars: int) -> list[Turn]:
    """The recent conversation, oldest first, ready to hand to a model.

    This is what makes JARVIS remember. Stage 0 stored every exchange and
    then never looked at it again; this reads it back so a conversation
    can continue across messages, and across days.

    Two limits, and both matter. `limit` caps how many turns are
    considered. `max_chars` caps how much text actually goes, because
    history is re-sent on every single message -- so a long conversation
    silently makes every future message more expensive. Trimming drops
    the oldest first, which is both the cheapest thing to forget and the
    least likely to be missed.
    """
    from app.db import fetch

    if limit <= 0 or max_chars <= 0:
        return []

    rows = await fetch(
        """
        SELECT content, origin FROM memories
        WHERE origin = ANY($1::text[])
        ORDER BY created_at DESC
        LIMIT $2
        """,
        list(_ORIGIN_ROLES),
        limit,
    )

    # Newest first out of the database, so the budget is spent on the most
    # recent turns; reversed at the end back into reading order.
    kept: list[Turn] = []
    remaining = max_chars
    for row in rows:
        text = row["content"]
        if len(text) > remaining:
            break
        remaining -= len(text)
        kept.append(Turn(role=_ORIGIN_ROLES[row["origin"]], text=text))

    kept.reverse()
    history = normalise_history(kept)
    if history:
        return history

    # Nothing survived. Two ways that happens, and both are real: one
    # message longer than the whole budget, or a budget so small that the
    # only turn that fit was a reply, which normalising then dropped for
    # starting mid-exchange.
    #
    # Returning nothing here would look like total amnesia when it is
    # really a size limit doing its job. So fall back to the single most
    # valuable thing: the last thing the OWNER said, trimmed to fit. Their
    # words matter more than JARVIS's own, and a user turn is the one
    # shape every provider accepts on its own.
    return _last_user_turn_trimmed(rows, max_chars)


def _last_user_turn_trimmed(rows: list, max_chars: int) -> list[Turn]:
    for row in rows:  # newest first
        if _ORIGIN_ROLES[row["origin"]] != USER:
            continue
        text = row["content"]
        if len(text) > max_chars:
            text = text[:max_chars] + "... [trimmed to stay within the memory budget]"
        return [Turn(role=USER, text=text)]
    return []
