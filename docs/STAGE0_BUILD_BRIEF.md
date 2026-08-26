# JARVIS — Stage 0 Build Brief
### For execution by Claude Code (claude.ai/code)
**Owner:** Sagar — non-coder, reviewing all changes in plain language, approving via GitHub PRs/branches
**Reference:** See `ARCHITECTURE.md` for full rationale — this document is the executable subset for Stage 0 only.

> Kept in the repo verbatim as the spec this build followed. See
> `STAGE0_STATUS.md` for what was actually delivered against this list,
> including the couple of places a judgment call was needed.

---

## 0. Ground Rules for This Build

- **Do not implement anything beyond what's listed in this brief.** No agents, no event bus, no Scientist/Auditor automation yet — those are later stages, described in the architecture doc for context only.
- **Explain every non-trivial decision in plain language** in commit messages and PR descriptions — the owner cannot read code and relies on these explanations to approve changes.
- **Nothing destructive** (dropping data, overwriting config, deleting infrastructure) without flagging it clearly first.
- **Budget-aware from day one**: total infra + API spend must stay well under ₹3,000–4,000/month during Stage 0-1. Prefer free-tier services wherever adequate. Flag anything with a cost implication before provisioning it.
- **Small, working increments** — each step below should leave the system in a runnable, demonstrable state before moving to the next.

---

## 1. Repository Setup

- New GitHub repo: `jarvis-core` (private)
- Structure:
  ```
  /core        — JARVIS Core service (API, orchestrator skeleton)
  /db          — schema, migrations
  /workers     — background worker skeleton (Stage 1, stub only for now)
  /infra       — deployment config (Dockerfile, Cloud Run config)
  /docs        — plain-language docs: what exists, what doesn't yet, how to deploy
  ```
- Include a root `README.md` written for a non-coder: what JARVIS is, what's built so far, what isn't, how to check if it's running.

## 2. Infrastructure to Provision

| Service | Choice | Notes |
|---|---|---|
| Compute | Google Cloud Run (or equivalent container platform) | Scales to zero when idle — minimizes cost |
| Database | Managed Postgres, smallest tier (e.g. Cloud SQL smallest instance, or Supabase free tier) | Prefer Supabase free tier initially if it meets needs — genuinely $0 at this scale |
| Auth | Firebase Auth | Free tier sufficient for single user |
| Push notifications | Firebase Cloud Messaging | Free |
| Secrets | Google Secret Manager (or platform equivalent) | Never commit secrets to the repo |

Containerize the core service (Docker) from the start — this is the portability requirement (must be redeployable to a Mac mini or other host later without a rewrite).

## 3. Database Schema (Stage 0 subset)

Implement these tables now, with the fields listed — even fields not yet used by Stage 0 logic, so later stages don't require schema migrations that break existing data:

**`memories`**
`id, content, category (enum: working/episodic/semantic/project/people/decision/task/preference/system), origin (enum: stated/retrieved/inferred/predicted), confidence (0-1), created_at, expires_at (nullable), related_memory_ids (array, nullable)`

**`tasks`**
`id, objective, status (enum: pending/in_progress/completed/failed/awaiting_approval), origin, assigned_agent (nullable, for Stage 1+), budget_tokens (nullable), budget_cost (nullable), created_at, updated_at, checkpoint_data (jsonb, nullable)`

**`audit_log`**
`id, actor (user/system/agent_name), action, category (enum: low_risk/medium_risk/high_risk), approved_by (nullable), outcome, cost, created_at`

**`approvals`**
`id, action_category, default_policy (enum: auto/ask_every_time/ask_above_threshold), threshold_value (nullable), updated_at`

Seed `approvals` with the owner's stated defaults:
- `research`: auto
- `drafting`: auto
- `publishing`: ask_every_time
- `spending`: ask_every_time
- `credentials_or_security`: ask_every_time

## 4. JARVIS Core — Stage 0 Scope

Build the smallest end-to-end loop, nothing more:

1. A simple API endpoint that accepts a message (text — voice/UI is the owner's domain, not built here)
2. Routes it to an LLM call (start with one provider adapter — Claude — but structure this behind an interface so a second provider can be added later without touching calling code)
3. Stores the exchange in `memories` with `origin = stated` for what the user said, and `origin = predicted` or `inferred` only if JARVIS adds interpretation — never blur the two
4. Returns the response
5. Every request/response is logged to `audit_log` as `category = low_risk`, `approved_by = null` (auto-approved, per the research/drafting default)

This is intentionally *not* agentic yet — no orchestration, no tool use. It's the foundation: identity, memory-with-provenance, and a working request/response loop, deployed and reachable from the internet (so the owner can test it from the iPad browser).

## 5. Budget Guardrail (build now, not later)

Simple but non-negotiable for Stage 0:
- A running monthly cost counter (can start as a manually-updated estimate if real-time billing API integration is complex — flag this trade-off to the owner rather than skipping it silently)
- Alert logic (can be a log line + optional email/push) at 50% and 80% of the ₹3,000–4,000 ceiling
- Document in `/docs` exactly what counts toward this budget and what doesn't

## 6. Definition of Done for Stage 0

- [ ] Repo created, structured, with plain-language README
- [ ] Core service deployed on Cloud Run (or equivalent), reachable via HTTPS
- [ ] Postgres provisioned with the schema above
- [ ] Firebase Auth wired — owner can log in from a browser
- [ ] One real request/response round-trip works end-to-end and is visible in `memories` and `audit_log` with correct provenance
- [ ] Budget tracking + alert thresholds in place
- [ ] `/docs` explains, in plain language: what's live, what it costs so far, how to check if it's running, how to shut it down entirely (emergency stop equivalent for Stage 0)

Do not proceed to Stage 1 (task engine, first agent, background worker) until this list is complete and the owner has confirmed the end-to-end loop works from their own device.

---

## Notes for Claude Code

- The owner cannot review raw code. Every PR description should explain, in 2-4 plain sentences, what changed and why — written for a mechanical engineer, not a developer.
- If any step in this brief turns out to be a bad idea once you're in the implementation (e.g., a better free-tier option than what's listed, a schema field that doesn't make sense), say so clearly rather than silently deviating — this is a stated principle of the project (surface better approaches, don't silently patch around them).
- Full context and long-term direction is in `JARVIS_Architecture_v1.md` — read it before starting, but do not implement beyond what's scoped in this brief.
