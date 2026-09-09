"""Put one agent to work, so the live half of the Agents page can be seen.

Nothing in a check server actually runs for long enough to catch an agent
mid-task, and "what is this agent doing right now" is the question the
page exists to answer. So one task is left running, with the model
routing event a real run would have written beside it.
"""
import asyncio, json, os, asyncpg

OBJECTIVE = "Find out what changed in Karnataka's EV policy this week"


async def main():
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    workflow = await pool.fetchval(
        "INSERT INTO workflows (objective, requested_by, status) "
        "VALUES ($1,'user:owner','running') RETURNING id",
        "Karnataka EV policy, checked")
    task = await pool.fetchval(
        "INSERT INTO tasks (workflow_id, objective, capability, status, "
        "started_at, budget_cost, attempts) "
        "VALUES ($1,$2,'research.web','running',now(),1.50,1) RETURNING id",
        workflow, OBJECTIVE)
    await pool.execute(
        "INSERT INTO agent_events (workflow_id, task_id, capability, kind, detail) "
        "VALUES ($1,$2,'research.web','model_routed',$3)",
        workflow, task,
        json.dumps({"tier": "standard", "model": "gemini-test",
                    "provider": "gemini",
                    "why": "standard is what the task asked for"}))
    await pool.close()
    print(task)

asyncio.run(main())
