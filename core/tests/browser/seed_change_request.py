"""A planned change and a proposed one, so the Build tab can be checked.

On a check server the model calls that plan and write code cannot run, so
the two states that matter are seeded: one plan waiting to be written,
and one proposal with a diff and failing tests. Failing on purpose --
tests that fail must look like tests that fail, and a seed where
everything passes would never show that.
"""
import asyncio, json, os, asyncpg

PLAN = {
    "summary": "Add a Telegram bridge so JARVIS can be reached by text.",
    "is_a_change": True,
    "title": "Telegram bridge",
    "files": [
        {"path": "core/app/telegram.py", "why": "the bridge itself", "new": True},
        {"path": "core/app/main.py", "why": "wire the router in", "new": False},
    ],
    "steps": ["Add the webhook route", "Check the sender is the owner",
              "Relay the message through the existing path"],
    "risk": ["An open webhook is a second front door into a system that spends money."],
    "cannot": ["This needs a Telegram bot token, which only you can create."],
    "tests": "A webhook from an unknown sender is refused.",
}

DIFF = """\
 core/app/telegram.py | 24 ++++++++++++++++++++++++
 1 file changed, 24 insertions(+)

diff --git a/core/app/telegram.py b/core/app/telegram.py
new file mode 100644
--- /dev/null
+++ b/core/app/telegram.py
@@ -0,0 +1,4 @@
+\"\"\"Reaching JARVIS by text.\"\"\"
+
+ALLOWED_CHAT_ID = None
+
"""


async def main():
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    await pool.execute("DELETE FROM change_requests")

    await pool.execute(
        "INSERT INTO change_requests (title, brief, state, plan) "
        "VALUES ($1,$2,'planned',$3)",
        "Money tab totals", "Show the month's totals on the Money panel.",
        json.dumps({**PLAN, "title": "Money tab totals",
                    "summary": "Show the month's totals on the Money panel."}))

    proposed = await pool.fetchval(
        "INSERT INTO change_requests (title, brief, state, reason, plan, branch, "
        "diff, files_changed, tests_passed, tests_output, spend_inr, shadow_inr) "
        "VALUES ($1,$2,'proposed',$3,$4,$5,$6,1,false,$7,0,4.20) RETURNING id",
        "Telegram bridge",
        "Add a Telegram bridge so JARVIS can be reached by text.",
        "1 file(s) written on jarvis/telegram-bridge. The tests FAIL -- "
        "read them before merging.",
        json.dumps(PLAN), "jarvis/telegram-bridge", DIFF,
        "E   ImportError: cannot import name 'telegram' from 'app.routes'\n"
        "1 failed, 643 passed in 16.02s")
    await pool.close()
    print(proposed)

asyncio.run(main())
