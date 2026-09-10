"""Leave work in flight, and some of it stuck.

A check server finishes everything instantly or fails instantly, so the
one state that mattered -- something running, and running too long -- can
never be caught in the act.
"""
import asyncio, os, asyncpg

async def main():
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    workflow = await pool.fetchval(
        "INSERT INTO workflows (objective, requested_by, status) "
        "VALUES ('Media: the benchmark everyone quotes','user:owner','running') "
        "RETURNING id")
    await pool.execute(
        "INSERT INTO tasks (workflow_id, objective, capability, status, "
        "started_at, finished_at) VALUES "
        "($1,'Research the benchmark','research.web','completed',"
        " now() - interval '22 minutes', now() - interval '20 minutes')",
        workflow)
    await pool.execute(
        "INSERT INTO tasks (workflow_id, objective, capability, status, started_at) "
        "VALUES ($1,'Verify the claims','factcheck.claims','running',"
        " now() - interval '19 minutes')", workflow)
    await pool.execute(
        "INSERT INTO tasks (workflow_id, objective, capability, status) "
        "VALUES ($1,'Write the script','media.script','blocked')", workflow)
    piece = await pool.fetchval(
        "INSERT INTO content_pieces (workflow_id, topic, brand, state, created_at) "
        "VALUES ($1,'the benchmark everyone quotes','ai_media','producing',"
        " now() - interval '22 minutes') RETURNING id", workflow)
    await pool.close()
    print(piece)

asyncio.run(main())
