# JARVIS

A personal AI operating system, built in stages. This repo currently holds
**Stage 0**: the foundation. Not a product yet — a working request/response
loop with real memory storage and an audit trail underneath it, so every
later stage has somewhere solid to stand.

Full rationale for the design lives in `docs/ARCHITECTURE.md` (the
reference architecture). This README is the "what's actually here right
now, and how do I check it" version.

## What Stage 0 is

You send JARVIS a text message → it calls Claude → it replies → the
exchange is saved to the database with a record of *where each piece of
information came from* (what you said vs. what JARVIS produced) → the
action is logged for audit. That's it. No agents, no background tasks, no
autonomous behaviour. Those are later stages, described in
`docs/ARCHITECTURE.md` but deliberately not built yet — see
`docs/STAGE0_STATUS.md` for the exact "what's live / what isn't" list.

## Repository layout

```
/core   — JARVIS Core service: the API, the memory/audit/budget logic,
          the LLM adapter, and a bare-bones browser test page
/db     — database schema (plain SQL migrations) and the tool that applies them
/workers — placeholder for Stage 1's background worker (not built yet)
/infra  — Dockerfile + local docker-compose setup
/docs   — plain-language docs: status, deployment steps, budget rules
```

## Is it running right now?

Nothing is deployed to the internet yet — see
`docs/STAGE0_STATUS.md` for exactly what state the project is in and why.
The code is complete and has been tested locally (a real Postgres
database, real HTTP requests, the full message → memory → audit →
budget → emergency-stop path all verified working). What's left is
**provisioning your own cloud accounts** (Supabase, Firebase, Google Cloud)
— steps I can't do for you without your credentials. `docs/DEPLOYMENT.md`
is the exact checklist; once you've done those steps (or handed me the
credentials to do it in a session), `GET /health` on the deployed URL
tells you it's alive.

## How to try it locally (no cloud account needed)

```bash
docker compose -f infra/docker-compose.yml up --build
# in another terminal, once postgres is healthy:
DATABASE_URL=postgres://jarvis:jarvis@localhost:5432/jarvis python3 db/migrate.py
```

Then open `http://localhost:8080` in a browser. Firebase login won't work
locally (no project configured), but with `DEV_MODE=true` (already set in
`infra/docker-compose.yml`) every request is treated as the owner, so you
can type a message straight into the test console and see it round-trip.
Without an `ANTHROPIC_API_KEY` set, JARVIS replies with an honest "no real
model was called" mock response instead of pretending — see
`core/app/llm/mock_adapter.py`.

## How to stop it entirely

- **Pause without tearing anything down**: `POST /v1/admin/emergency-stop`
  with `{"stop": true}`. JARVIS immediately refuses all new requests
  (`GET /health` shows `emergency_stop: true`) until you turn it back off.
  Nothing is deleted; this is reversible in one call.
- **Actually shut it down**: once deployed, stop or delete the Cloud Run
  service from the Google Cloud Console. Your data stays in Postgres
  either way. See `docs/STAGE0_STATUS.md`.

## Documents

- `docs/ARCHITECTURE.md` — the full reference architecture and staged roadmap
- `docs/STAGE0_STATUS.md` — what's live, what it costs, how to check, how to stop it
- `docs/DEPLOYMENT.md` — step-by-step cloud provisioning checklist
- `docs/BUDGET.md` — exactly what counts toward the monthly spend estimate
