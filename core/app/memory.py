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
from uuid import UUID

from app.db import fetchrow

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


async def link_memories(a: UUID, b: UUID) -> None:
    """Record that two memories relate to each other (e.g. a message and its reply)."""
    from app.db import execute

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
