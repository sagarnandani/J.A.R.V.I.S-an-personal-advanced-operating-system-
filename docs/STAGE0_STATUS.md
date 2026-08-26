# Stage 0 status

Plain-language answer to: what exists, what doesn't, what it costs, how do
I check on it, how do I stop it.

## What's live

**Nothing is deployed to the internet yet.** I (Claude Code) don't have
access to your Google Cloud, Supabase, or Firebase accounts — provisioning
real infrastructure needs your credentials, which I was never given in
this session. What I *have* done is write and locally test the complete
Stage 0 codebase:

- A real Postgres database schema, applied and verified against an actual
  Postgres instance.
- The full JARVIS Core API, run locally and exercised end-to-end over real
  HTTP requests: sending a message, getting a reply, seeing it recorded in
  `memories` with correct provenance, seeing it logged in `audit_log`,
  reading back the budget estimate, toggling Emergency Stop and confirming
  it actually blocks requests.
- 9 automated tests, all passing (`core/tests/`).

None of that required a cloud account — it ran against a local Postgres
and, because no `ANTHROPIC_API_KEY` was configured, a mock LLM adapter
that honestly labels its replies as fake rather than pretending. See
`core/app/llm/mock_adapter.py`.

**What's left to go from "tested locally" to "reachable from your iPad"**
is the provisioning checklist in `DEPLOYMENT.md` — creating the actual
Supabase/Firebase/Cloud Run accounts and resources, and setting the real
`ANTHROPIC_API_KEY`. I can either walk you through that checklist, or, if
you'd rather I do it directly, you can share the relevant credentials in a
session and I'll run it.

## What isn't built (on purpose)

Per the Stage 0 Build Brief's ground rule ("do not implement anything
beyond what's listed in this brief"):

- No agents, no orchestration, no tool use
- No task engine, no background workers (the `/workers` folder is an
  empty placeholder for Stage 1)
- No event bus, no Attention Engine
- No Governor/Auditor/Scientist automation (their database seams —
  `approvals`, `audit_log` categories — exist, but nothing acts on them
  beyond the one auto-approve rule Stage 0 needs)
- No real-time cloud billing integration (see `BUDGET.md` for what the
  budget tracker actually measures instead, and why)
- No email/push budget alerts — threshold crossings are logged, not yet
  sent anywhere (flagged here rather than silently skipped, per the
  brief's own instruction)

## What it costs so far

**₹0.** Nothing is provisioned. Once deployed on the free/scale-to-zero
tiers named in `DEPLOYMENT.md`, expected cost at solo, low-volume usage is
still close to ₹0 for compute/database/auth — the only real variable cost
is Anthropic API usage per message, tracked by `GET /v1/budget` once
deployed. See `BUDGET.md` for exactly what that number does and doesn't
include.

## How to check if it's running

Once deployed:

```bash
curl https://<your-cloud-run-url>/health
```

`{"status": "ok", "emergency_stop": false, "dev_mode": false}` means
JARVIS is up, accepting requests, and not in dev mode (dev mode must never
be true on a real deployment — it disables login).

Locally, the same check is `http://localhost:8080/health` after
`docker compose -f infra/docker-compose.yml up`.

The test console at the root URL (`/`) also gives you a sign-in button and
a message box — the fastest way to eyeball that the whole loop works from
a browser, including your iPad's.

## How to stop it

Two levels, cheapest first:

1. **Emergency Stop** (reversible, instant, keeps everything running and
   billed as normal — it just refuses to process messages):
   ```bash
   curl -X POST https://<your-cloud-run-url>/v1/admin/emergency-stop \
     -H "Authorization: Bearer <your Firebase ID token>" \
     -H "Content-Type: application/json" \
     -d '{"stop": true}'
   ```
   (The test console has buttons for this too, once wired up in a later
   pass — for now it's a plain API call. `GET /health` confirms the state.)

2. **Full shutdown** (stops billing entirely): stop or delete the Cloud
   Run service from the Google Cloud Console. Your data isn't touched —
   it lives in Postgres, separately. Redeploying later (from this same
   repo, same Docker image) brings JARVIS back exactly as it was.

There is deliberately no "delete the database" step in either of these —
Stage 0 never destroys data as part of stopping the service.
