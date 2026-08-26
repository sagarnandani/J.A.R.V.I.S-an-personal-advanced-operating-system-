"""Validation happens before any database call, so these run without a DB.

The thing being protected here is the provenance rule the whole memory
system exists for: origin values are constrained to the four the
architecture doc defines, and nothing else can silently sneak into that
column.
"""
import pytest

from app.memory import store_memory


@pytest.mark.asyncio
async def test_rejects_unknown_category():
    with pytest.raises(ValueError, match="category"):
        await store_memory(content="x", category="not_a_real_category", origin="stated")


@pytest.mark.asyncio
async def test_rejects_unknown_origin():
    with pytest.raises(ValueError, match="origin"):
        await store_memory(content="x", category="episodic", origin="hallucinated")


@pytest.mark.asyncio
async def test_rejects_out_of_range_confidence():
    with pytest.raises(ValueError, match="confidence"):
        await store_memory(
            content="x", category="episodic", origin="stated", confidence=1.5
        )
