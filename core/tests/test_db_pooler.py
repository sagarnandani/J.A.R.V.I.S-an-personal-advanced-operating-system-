"""The connection-pooler compatibility rule.

Supabase's transaction pooler can't do prepared statements, which asyncpg
uses by default -- a mismatch that fails confusingly at runtime rather
than at startup. These pin the detection so a future change can't quietly
reintroduce that failure.
"""
from app.db import _pool_kwargs

SUPABASE_TXN_POOLER = (
    "postgresql://postgres.ggnyypoopkmhfgtbqync:pw"
    "@aws-0-ap-south-1.pooler.supabase.com:6543/postgres"
)
SUPABASE_SESSION_POOLER = (
    "postgresql://postgres.ggnyypoopkmhfgtbqync:pw"
    "@aws-0-ap-south-1.pooler.supabase.com:5432/postgres"
)
SUPABASE_DIRECT = (
    "postgresql://postgres:pw@db.ggnyypoopkmhfgtbqync.supabase.co:5432/postgres"
)
LOCAL = "postgres://jarvis:jarvis@localhost:5432/jarvis"


def test_transaction_pooler_disables_statement_cache():
    assert _pool_kwargs(SUPABASE_TXN_POOLER) == {"statement_cache_size": 0}


def test_session_pooler_keeps_default_cache():
    assert _pool_kwargs(SUPABASE_SESSION_POOLER) == {}


def test_direct_connection_keeps_default_cache():
    assert _pool_kwargs(SUPABASE_DIRECT) == {}


def test_local_connection_keeps_default_cache():
    assert _pool_kwargs(LOCAL) == {}
