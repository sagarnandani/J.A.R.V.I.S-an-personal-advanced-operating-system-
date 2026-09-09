"""Put two finished media pieces in the database: one ready, one declined.

The mock provider cannot write a script or a review, so the Media tab's
richest rendering -- cited sections, the reviewer's must-fix list, both
cost figures, and the approve button -- would otherwise never be seen
before the owner sees it.

The declined one matters as much as the ready one. Deciding against
publishing is the most valuable thing the strategist does, and the panel
has to show it as a decision rather than as a fault.
"""
import asyncio, json, os, asyncpg

PACKAGE = {
    "summary": "3-section script for AI Media, ~95s",
    "brand": "ai_media",
    "title_options": ["What the new model actually changes",
                      "The benchmark everyone is quoting wrong"],
    "hook": "The number in every headline this week is not the number in the paper.",
    "sections": [
        {"beat": "What happened", "cites": ["The lab published a model card on Tuesday."],
         "text": "A new model shipped on Tuesday with a published model card."},
        {"beat": "What is confirmed", "cites": ["The card reports 71.2 on the benchmark."],
         "text": "The card reports 71.2. Coverage has been rounding that to 'about 80'."},
        {"beat": "What to watch", "cites": [],
         "text": "Independent reproductions are the thing to wait for."},
    ],
    "cta": "Subscribe if you want the checked version rather than the fast one.",
    "thumbnail_concept": "The quoted number crossed out, the real one beside it.",
    "estimated_seconds": 95,
}

REVIEW = {
    "verdict": "pass", "why": "Every factual line is tied to the card.",
    "scores": {"factual_accuracy": 0.9, "hype": 0.9, "clarity": 0.8},
    "must_fix": [], "should_fix": ["The third section could name one lab."],
    "unsupported_claims": [], "weakest": [],
}


async def piece(pool, *, topic, state, reason, package, review, title,
                spend, shadow):
    wf = await pool.fetchval(
        "INSERT INTO workflows (objective, requested_by, status) "
        "VALUES ($1,'user:owner','completed') RETURNING id", f"Media: {topic}")
    await pool.execute(
        "INSERT INTO tasks (workflow_id, objective, capability, status, result, "
        "confidence, spend_inr, shadow_inr, finished_at) "
        "VALUES ($1,$2,'media.script','completed',$3,0.8,$4,$5,now())",
        wf, f"Write the script: {topic}", json.dumps({"output": package or {}}),
        spend, shadow)
    return await pool.fetchval(
        "INSERT INTO content_pieces (workflow_id, topic, brand, state, reason, "
        "title, package, review, spend_inr, shadow_inr) "
        "VALUES ($1,$2,'ai_media',$3,$4,$5,$6,$7,$8,$9) RETURNING id",
        wf, topic, state, reason, title,
        json.dumps(package) if package else None,
        json.dumps(review) if review else None, spend, shadow)


async def main():
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    ready = await piece(
        pool, topic="The benchmark number everyone is quoting",
        state="ready", reason="Passed review. Waiting for your approval.",
        package=PACKAGE, review=REVIEW,
        title=PACKAGE["title_options"][0], spend=0, shadow=4.20)
    await piece(
        pool, topic="A tool launch with nothing behind it",
        state="declined",
        reason="Decided against publishing: nothing here a viewer would not "
               "get from the headline.",
        package=None, review=None, title=None, spend=0, shadow=1.10)
    await pool.close()
    print(ready)

asyncio.run(main())
