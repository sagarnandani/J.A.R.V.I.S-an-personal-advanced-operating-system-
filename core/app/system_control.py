"""Emergency Stop.

A single flag, checked before any LLM call, that halts JARVIS's response
loop when set. Architecture doc, section F: "build this early, not
eventually -- it's cheap now and expensive to retrofit once agents are
running unattended." Stage 0 has no agents to stop, but the flag and the
check are wired through now so nothing later needs retrofitting.
"""
from app.db import execute, fetchrow


async def is_stopped() -> bool:
    row = await fetchrow("SELECT value FROM system_control WHERE key = 'emergency_stop'")
    return bool(row["value"]) if row else False


async def set_stopped(stopped: bool) -> None:
    await execute(
        """
        INSERT INTO system_control (key, value, updated_at)
        VALUES ('emergency_stop', $1, now())
        ON CONFLICT (key) DO UPDATE SET value = $1, updated_at = now()
        """,
        stopped,
    )
