"""Give one capability a measured track record, per model.

A check server finishes everything instantly with the mock provider, so
it never accumulates the thing section 15 is about: enough runs, on more
than one model, for a difference between them to be real. Both halves
are seeded -- a model doing badly with enough runs behind it to say so,
and a model doing badly with too few, which the page must refuse to
judge. The second is the one that catches a panel that has started
believing small numbers.
"""
import asyncio, os, asyncpg

CAPABILITY = "research.web"
# 2 wins in 12 -- 17%, well under the 60% floor, on more than the 8 runs
# it takes before JARVIS will judge a model at all.
FAILING = ("gemini-test", 2, 10)
# 0 wins in 3. Deliberately a WORSE headline rate than the model above,
# on far too few runs to mean anything. If the "enough runs" floor ever
# stopped being applied, this is the model that would be picked as the
# one to route around -- so the check has something to catch.
UNJUDGED = ("gemini-test-pro", 0, 3)


async def main():
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    await pool.execute("DELETE FROM agent_metrics WHERE capability = $1", CAPABILITY)
    for model, wins, losses in (FAILING, UNJUDGED):
        for metric, value, n in (("success", 1, wins), ("success", 0, losses)):
            for _ in range(n):
                await pool.execute(
                    "INSERT INTO agent_metrics (capability, metric, value, "
                    "provider, model, tier) VALUES ($1,$2,$3,'gemini',$4,'standard')",
                    CAPABILITY, metric, value, model)
        await pool.execute(
            "INSERT INTO agent_metrics (capability, metric, value, provider, "
            "model, tier) VALUES ($1,'latency_ms',1400,'gemini',$2,'standard')",
            CAPABILITY, model)
    await pool.close()
    print(f"{CAPABILITY}: {FAILING[0]} judged, {UNJUDGED[0]} not")

asyncio.run(main())
