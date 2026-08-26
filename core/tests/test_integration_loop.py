"""End-to-end: store a memory exchange and an audit row against a real
Postgres, then read them back -- the same path /v1/message exercises.

Requires a database with the Stage 0 schema applied (see
infra/docker-compose.yml + db/migrate.py). Skips itself cleanly if no
database is reachable at DATABASE_URL.
"""
from decimal import Decimal

import pytest

from app import audit, memory


@pytest.mark.asyncio
async def test_stores_exchange_with_correct_provenance(db_pool):
    user_id = await memory.store_memory(
        content="hello jarvis", category="episodic", origin="stated"
    )
    reply_id = await memory.store_memory(
        content="hello sagar", category="episodic", origin="retrieved"
    )
    await memory.link_memories(user_id, reply_id)

    recent = await memory.list_recent(limit=5)
    by_id = {r["id"]: r for r in recent}

    assert by_id[user_id]["origin"] == "stated"
    assert by_id[reply_id]["origin"] == "retrieved"
    assert reply_id in by_id[user_id]["related_memory_ids"]
    assert user_id in by_id[reply_id]["related_memory_ids"]


@pytest.mark.asyncio
async def test_logs_low_risk_auto_approved_audit_entry(db_pool):
    audit_id = await audit.log_audit(
        actor="system",
        action="llm_message_exchange",
        category="low_risk",
        outcome="success",
        cost=Decimal("1.23"),
    )

    recent = await audit.list_recent(limit=5)
    row = next(r for r in recent if r["id"] == audit_id)

    assert row["category"] == "low_risk"
    assert row["approved_by"] is None
    assert row["outcome"] == "success"
