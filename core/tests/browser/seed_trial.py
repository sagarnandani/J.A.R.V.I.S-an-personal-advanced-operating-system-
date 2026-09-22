"""A new version of an agent, half way through being tried.

A check server promotes nothing and measures nothing, so the one state
the panel exists to show -- two versions running side by side with real
numbers against each of them -- has to be seeded. Both halves are here:
enough runs on each arm for a verdict, and a gap that is real rather
than noise, because "not enough yet" and "no difference worth acting on"
are the two answers most likely to be got wrong.
"""
import asyncio, os, asyncpg
from decimal import Decimal

CAPABILITY = "research.web"
CANDIDATE, BASELINE = 2, 1


async def main():
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    await pool.execute("DELETE FROM agent_trials WHERE capability = $1", CAPABILITY)

    live = await pool.fetchrow(
        "SELECT * FROM agents WHERE capability = $1 AND status = 'active'",
        CAPABILITY)
    if live is None:
        print(f"No live {CAPABILITY}; start the server once first.")
        return

    # A second version, identical but for its number. Registered as
    # 'testing': routable only when asked for by name, which is what a
    # candidate is.
    candidate = await pool.fetchval(
        """
        INSERT INTO agents (capability, version, name, description, domain,
                            supervisor, task_types, tools, permissions,
                            model_tiers, status, max_cost_inr, config)
        SELECT capability, $2, name || ' v2', description, domain, supervisor,
               task_types, tools, permissions, model_tiers, 'testing',
               max_cost_inr, config
          FROM agents WHERE id = $1
        ON CONFLICT (capability, version) DO UPDATE SET status = 'testing'
        RETURNING id
        """,
        live["id"], CANDIDATE)

    await pool.execute(
        "INSERT INTO agent_trials (capability, candidate_version, "
        "baseline_version, share, started_by) VALUES ($1,$2,$3,0.20,'user:owner')",
        CAPABILITY, CANDIDATE, BASELINE)

    # 18/20 against 11/20: a 35-point gap, well past the margin.
    for agent_id, wins, losses in ((candidate, 18, 2), (live["id"], 11, 9)):
        for value, n in ((1, wins), (0, losses)):
            for _ in range(n):
                await pool.execute(
                    "INSERT INTO agent_metrics (capability, agent_id, metric, value) "
                    "VALUES ($1,$2,'success',$3)",
                    CAPABILITY, agent_id, Decimal(str(value)))

    await pool.close()
    print(f"{CAPABILITY}: v{CANDIDATE} (90%) against v{BASELINE} (55%)")

asyncio.run(main())
