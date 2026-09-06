"""Put a realistic finished research+factcheck workflow in the database.

The mock provider cannot produce one, so the panel's richest rendering --
verdicts, evidence links, a contradiction -- would otherwise never be seen
before the owner sees it.
"""
import asyncio, json, os, asyncpg

RESEARCH = ("Karnataka's 2024 EV policy waives road tax on electric two-wheelers "
            "and offers a purchase subsidy. Reports differ on the ceiling.")

CLAIMS = {
    "summary": "Checked 3 claim(s): 1 supported, 1 contradicted, 1 unverified.",
    "claims": [
        {"claim": "Karnataka waives road tax on electric two-wheelers",
         "verdict": "supported", "confidence": 0.9,
         "why": "Stated in the state's 2024 EV policy document.",
         "sources": ["Karnataka Govt — https://karnataka.gov.in/ev-policy",
                     "The Hindu — https://thehindu.com/ev-karnataka"]},
        {"claim": "The purchase subsidy ceiling is Rs.1,50,000",
         "verdict": "contradicted", "confidence": 0.9,
         "why": "Two outlets give Rs.50,000 as the ceiling.",
         "sources": ["Economic Times — https://economictimes.com/ev-subsidy"]},
        {"claim": "The scheme runs until 2030",
         "verdict": "unverified", "confidence": 0.2,
         "why": "No source addressed the end date.", "sources": []},
    ],
}


async def main():
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    wf = await pool.fetchval(
        "INSERT INTO workflows (objective, requested_by, status) "
        "VALUES ($1,$2,'completed') RETURNING id",
        "Karnataka EV subsidy, checked", "user:owner")

    a = await pool.fetchval(
        "INSERT INTO tasks (workflow_id, objective, capability, status, result, "
        "confidence, spend_inr, finished_at) "
        "VALUES ($1,$2,'research.web','completed',$3,0.85,0.42,now()) RETURNING id",
        wf, "Find the Karnataka EV subsidy terms",
        json.dumps({"output": RESEARCH,
                    "evidence": ["Karnataka Govt — https://karnataka.gov.in/ev-policy"],
                    "assumptions": [], "unresolved": []}))
    await pool.execute(
        "INSERT INTO tasks (workflow_id, objective, capability, status, result, "
        "confidence, spend_inr, depends_on, finished_at) "
        "VALUES ($1,$2,'factcheck.claims','completed',$3,0.67,0.88,$4,now())",
        wf, "Check the findings",
        json.dumps({"output": CLAIMS,
                    "evidence": ["Karnataka Govt — https://karnataka.gov.in/ev-policy",
                                 "Economic Times — https://economictimes.com/ev-subsidy"],
                    "assumptions": ["Claims split by gemini-3.6-flash"],
                    "unresolved": ["The scheme runs until 2030 — no source addressed the end date."]}),
        [a])
    await pool.close()
    print(wf)

asyncio.run(main())
