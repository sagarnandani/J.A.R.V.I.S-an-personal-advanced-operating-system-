# JARVIS — Architectural Analysis
### Personal AI Operating System — First-Principles Design
**Prepared for:** Sagar
**Context:** Solo build, non-coder, iPad-primary, fully AI-implemented (Claude Code + this analysis as the reference architecture)

> This is the reference architecture for the whole project. Stage 0 (this
> repo, as it currently stands) implements only a small slice of it — see
> `STAGE0_STATUS.md` for exactly what's built. Everything else described
> below is context for where the project is headed, not a claim about
> what exists today.

---

## A. Interpretation

JARVIS is not a chatbot and not a collection of automations. It is a **personal operating system** — a persistent, single-identity intelligence that sits between you and everything else (models, tools, agents, apps, data), so that you only ever interact with *JARVIS*, never with the machinery underneath.

Three things distinguish it from "a smart assistant app":

1. **It has continuity.** The same JARVIS remembers you tomorrow, on a different device, using a different underlying model, in the middle of a task that started last week.
2. **It has authority structure.** It can act — including autonomously — but only inside boundaries you define, with escalating approval requirements as risk increases.
3. **It has legitimate uncertainty.** It distinguishes what you told it, from what it inferred about you, from what it's guessing. It says "I'm not sure" instead of confidently making things up.

Given your situation — non-coder, solo, iPad-primary — the practical interpretation is: **build a small number of genuinely correct foundational pieces first, keep every seam swappable, and never let convenience (Firebase, a single chat session, a clever shortcut) leak into becoming the architecture itself.**

---

## B. First-Principles Architecture

```
You
 │
 ▼
JARVIS Core  ──────────────────────────────────────────┐
 │  • Identity & continuity                             │
 │  • Memory (working / episodic / semantic / project /  │
 │    people / decision / task / preference / system)    │
 │  • Planning & decision layer (Strategist)              │
 │  • Orchestration                                       │
 └───────────┬─────────────────────────────────────────┘
             │
             ▼
      Agents (specialists)
             │
             ▼
      Tools / Models / Services  (behind adapters)
             │
             ▼
         Execution
             │
             ▼
   Verification → Learning (Scientist) → Memory/Audit (Auditor)
```

Governance wraps the whole thing, not just one layer:

- **Governor** — enforces permissions, approval policy, autonomy limits
- **Auditor** — independently checks safety, reliability, truthfulness
- **Scientist** — improves capability using evidence, inside sandboxes
- **Strategist** — keeps work aligned to your actual goals, not just task completion

None of these four need to be separate expensive LLM agents running all the time. Early on, most of them are deterministic logic + policy checks + scheduled review passes. They become more AI-driven only once there's enough real usage data to justify it.

---

## C. Major Subsystems

| Subsystem | Responsibility |
|---|---|
| **JARVIS Core** | Identity, session continuity, request routing |
| **Memory System** | Store/recall/correct/forget, with provenance and confidence |
| **Task Engine** | Durable tasks that outlive a conversation |
| **Orchestrator** | Decides which agent/tool/model handles what |
| **Agent Registry** | What agents exist, their permissions, versions |
| **Capability Registry** | What tools/models/skills are currently usable |
| **Intelligence Router** | Cheapest adequate solution for each sub-problem |
| **Event Bus** | Reacts to things happening (email, deadlines, task completion) |
| **Attention Engine** | Decides what interrupts you vs. waits vs. is discarded |
| **Approval Engine** | Structured yes/no/limits, not a single global switch |
| **Governor** | Policy enforcement, autonomy levels |
| **Auditor** | Safety/quality review, can block or escalate |
| **Scientist** | Controlled self-improvement, sandbox-tested |
| **Observability Layer** | Plain-language control center |
| **Security Layer** | Auth, isolation, secrets, prompt-injection defense |

---

## D. Runtime Architecture

**Clients** (iPad app, phone, future web/desktop) are *thin*. They hold no core logic and no core state — they authenticate, display, capture voice/text input, and show notifications. This is the single most important decision for your portability goal: if the client ever "knows" business logic, moving JARVIS later means rewriting the client too.

**Backend** is a containerized service (or small set of services) doing the actual thinking: JARVIS Core, orchestrator, memory access, task engine. It runs on a cloud VM/container platform today, and — because it's a container talking to a standard database — can run on a Mac mini or home server later with no rewrite, just a redeploy.

**Workers** handle background/long-running execution (research jobs, content generation, scheduled checks) so a locked iPad never blocks progress. These read from the same task queue the core writes to.

**Agents** are not separate servers in the early stage — they're structured prompts + tool access + permission scopes, invoked by the orchestrator. They only become independent services once one of them needs isolation for security or reliability reasons.

**Event system** (even a simple one at first — a queue/pub-sub) lets things happen *to* JARVIS (an email arrives, a deadline nears) not just *from* you.

```
[iPad / iPhone / Web] ──auth/API── [Backend API + Core] ──┬── [Postgres: memory/tasks/state]
        │                                │                 ├── [Task Queue]
   [Firebase: auth,                      │                 └── [Event Bus]
    push, live sync]                     ▼
                                   [Background Workers] ── [Agents] ── [Tools/Models via adapters]
```

---

## E. Data Architecture

**Core store: Postgres** (managed — Cloud SQL / Supabase / similar). Relational, portable, boring — which is exactly right for state you can't afford to lose or fight with. A vector store (for semantic memory search) sits alongside it later — pgvector is the simplest path, since it lives *inside* Postgres and doesn't add a second system to migrate.

Rough entity shape (not final schema, but the categories the spec calls for):

- `memories` — content, category (working/episodic/semantic/project/people/decision/preference/system), **origin** (stated / retrieved / inferred / predicted), confidence, timestamp, expiry/revalidation, links to related memories
- `tasks` — objective, status, assigned agent, subtasks, dependencies, budget, checkpoints, approval state
- `agents` — identity, version, permissions, trust/performance history
- `events` — source, type, payload, decision taken (ignored/recorded/escalated/etc.)
- `audit_log` — every autonomous or sensitive action, who/what approved it, outcome
- `approvals` — policy per action category, per agent, thresholds

Memory provenance is not a nice-to-have field — it's the fix for the exact issue you hit in the ChatGPT build. "Sagar told me X" and "I inferred X about Sagar" must never collapse into the same row with the same weight.

---

## F. Security Architecture

Given you can't personally review code, security has to be structural, not "trust the review":

- **Auth**: Firebase Auth (or equivalent) for you and any future collaborators — solved problem, don't build it yourself.
- **Secrets**: stored in a dedicated secrets manager, never in prompts, memory, or logs. Agents call an internal "send email" capability — they never hold the actual email credential.
- **Isolation**: each agent gets only the tool permissions its job requires (least privilege). A compromised or buggy content agent should not be able to touch financial tools or system config.
- **Prompt-injection defense**: anything JARVIS reads from the outside world (web pages, emails, documents) is treated as *data*, never as *instructions*. A malicious line in a scraped webpage saying "ignore previous instructions" must not be executable — this is enforced structurally (separate data/instruction channels), not by hoping the model resists it.
- **Approvals & audit**: every autonomous action above a low-risk threshold is logged, and higher-risk categories (publishing, spending, credentials, security config) require your explicit yes — by design, not by good intentions.
- **Emergency stop**: a single control (button/voice command) that halts all autonomous execution immediately. Build this early, not "eventually" — it's cheap now and expensive to retrofit once agents are running unattended.

---

## G. Agent Architecture

Each agent is defined by: identity, purpose, permitted tools, model choice, resource/task limits, trust level, version. Early on, agents are created **only** where a task genuinely benefits from specialization (per spec section 14) — resist creating a "Calendar Agent" and a "Reminder Agent" and a "Scheduling Agent" when one "Time & Scheduling Agent" does the job.

Communication between agents is structured, not free-form chat — a task object with objective, context, required output schema, confidence, cost, and completion status. This keeps agent-to-agent interaction observable and stoppable, and prevents the "two agents talking in circles forever" failure mode.

**Resource protection** is mandatory from day one, not a later hardening pass: every task gets a token budget, time budget, and max retry count. Without this, a single bad loop can burn real money before anyone notices — a real risk for a solo non-technical operator who won't be watching logs in real time.

---

## H. Scientist Architecture

The Scientist's job is narrow: **observe → hypothesize → test in a sandbox → benchmark against the current version → recommend or deploy within its authority → monitor → roll back if it regresses.**

Concretely, at your stage, this starts as something much simpler than an autonomous AI researcher: a scheduled review that looks at task success rate, cost, and your corrections, and surfaces plain-language suggestions ("the content agent fails 30% of the time when posting to Instagram — want me to investigate?"). Full autonomous self-improvement (agent rewriting itself, promoting new versions without you) is a later-stage capability, gated behind the Auditor and Governor being solid first.

---

## I. Auditor / Governor Architecture

**Governor** enforces the rules: autonomy levels per action category, spending limits, which actions need approval vs. run freely. This is config-driven — a policy table, not a model making judgment calls about its own authority.

**Auditor** independently checks: did this action match policy, did output quality degrade, are there signs of prompt injection or unexpected behavior, is cost trending abnormally. It can block a pending action or flag one that already happened. Early on this is largely rule-based checks + periodic review; it doesn't need to be a sophisticated AI agent to catch the failure modes that matter most (budget overruns, permission creep, repeated task failures).

Together, these two mean JARVIS's growing autonomy is always operating *inside* a boundary that was set by you, checked by rules, not by JARVIS's own judgment about itself — which is the core protection for someone who can't review the code directly.

---

## J. Cost Architecture

**Intelligence Router**: not every request needs your most expensive model. Order of preference for any given sub-task: deterministic code > cached/stored result > database lookup > small/cheap model > specialist model > frontier reasoning model > multi-agent > you (escalation). This alone will be the biggest cost lever you have.

**Attribution**: every cost (tokens, API calls, compute) tags back to a task/project/agent, so the plain-language dashboard can eventually say "your YouTube content agent cost $40 this month" rather than one opaque total bill.

**Budgets**: soft limits per task and per agent, hard limits per day/month overall, with JARVIS notifying you *before* a budget is blown, not after.

---

## K. Background Runtime

This is where the iPad constraint actually gets solved, and it only works if the backend is cloud-hosted and stateless-from-the-client's-perspective from day one (which section D above already established). The client app is just a window into a JARVIS that's always running server-side:

- Tasks are submitted to a queue, not held in the app's memory
- Workers process the queue independently of any device being open
- Push notifications (via Firebase) tell you when something needs your attention
- Reopening the app on any device shows current state, not "where you left off in this session"

Practically: your iPad locking, your phone dying, or you switching to a laptop later should never pause a running JARVIS task. This is achievable at small scale with a single always-on backend container plus a queue — no need for elaborate distributed infrastructure yet.

---

## L. Failure and Recovery

- **Checkpointing**: long tasks save progress periodically, so a crash resumes from the last checkpoint, not from zero
- **Versioning & rollback**: agents and their prompts/config are versioned; a bad change rolls back to the last known-good version automatically if the Auditor detects degraded performance
- **Backups**: automated daily Postgres backups (most managed providers do this by default — verify it's on) plus periodic export snapshots you could, in principle, hand to any other infrastructure
- **Graceful degradation**: if a model provider is down, JARVIS should say so plainly and either fall back to an alternate model or pause that category of work — never silently produce degraded output pretending nothing's wrong
- **Disaster recovery**: because core state lives in a portable Postgres database and the runtime is a container, "the cloud provider disappeared" is a bad afternoon (restore backup, redeploy container elsewhere), not a rebuild-from-scratch event

---

## M. Recommended Technology Categories

| Category | Recommendation | Why |
|---|---|---|
| Compute (core + workers) | Containerized service on Cloud Run / a small VM | Portable to Mac mini/home server later without rewrite |
| Primary database | Postgres (managed) | Relational fit for memory/tasks/audit; trivial to migrate |
| Vector/semantic search | pgvector (inside Postgres) | Avoids a second system to keep in sync/migrate |
| Client auth, push, live sync | Firebase Auth + Cloud Messaging | Genuinely Firebase's strength; client-only, replaceable |
| Task queue | Managed queue (Cloud Tasks) or simple Postgres-backed queue | Don't over-engineer this at solo scale |
| Secrets | Cloud secrets manager | Never in code/prompts/logs |
| LLM access | Provider-agnostic adapter layer (Claude, GPT, others as needed) | Section 3's requirement — no architectural lock-in |
| Observability | Simple plain-language dashboard (built by us) over the audit/task tables | Purpose-built for a non-coder, not raw logs |

None of these are exotic. The goal at your stage is boring, managed, and replaceable — not maximally powerful.

---

## N. Architecture Risks (Self-Critique)

- **Overengineering risk**: this spec describes an enterprise-grade system. Building all 65 sections before using JARVIS for anything real would mean months of infrastructure with zero payoff. Mitigation: the staged roadmap below deliberately defers most of it.
- **Cost risk**: multi-agent + frontier models + unbounded background execution can get expensive fast, especially unsupervised. Mitigation: hard budgets and the Intelligence Router are first-stage requirements, not later polish.
- **Security risk**: as a non-coder, you can't personally audit what gets built. Mitigation: lean harder on managed services with their own security track record, keep the approval/audit layer strict-by-default, and treat every "autonomous" capability as opt-in per category, not on by default.
- **Reliability risk**: background workers, event systems, and multi-step tasks introduce more failure surface than a simple chatbot. Mitigation: checkpointing and conservative retry limits from day one.
- **Scalability risk**: mostly not yours yet — one user, modest agent count. Real risk is the opposite: building for scale you don't need yet. Mitigation: explicitly deferred in the roadmap.
- **AI-specific risk**: hallucinated "facts" entering memory as if they were things you said; prompt injection from web content; an agent quietly drifting from its intended behavior. Mitigation: provenance-tagged memory, structured data/instruction separation, Auditor checks.
- **Migration risk**: even with containers + Postgres, moving *any* running system has friction (DNS, environment config, secrets). Mitigation: document the deploy process as we build it, so "move to Mac mini" is a checklist, not an unknown.

---

## O. Staged Roadmap

**Stage 0 — Foundation (build now)**
Containerized backend skeleton, Postgres schema (memory/tasks/agents/audit with provenance fields from the start), Firebase auth wired to the backend, one working end-to-end path: you send JARVIS a request (voice or text) → it responds → the exchange is stored as memory with correct provenance. No agents yet. This alone replaces "chatting with ChatGPT" with an actual JARVIS Core.

**Stage 1 — Core Loop**
Task engine (durable tasks, not just chat turns), one real background worker, one real agent (pick your highest-value use case — likely content/research given your YouTube plans), basic Approval Engine (simple category-based yes/no), plain-language observability dashboard.

**Stage 2 — Governance**
Governor policy table, Auditor rule-based checks, budgets and cost attribution, Emergency Stop. This stage exists specifically to make Stage 3's autonomy safe.

**Stage 3 — Expansion**
Additional agents as real needs justify them (per section 14 — resist proliferation), event bus for reactive behavior (email/deadline triggers), Attention Engine tuning, multi-device continuity polish.

**Stage 4 — Self-Improvement (later, deliberately)**
Scientist doing real experimentation and versioned agent promotion, richer Strategist behavior, deeper world model/digital twin. Nothing here starts until Stage 2's guardrails are proven in daily use.

**Designed now, built later:** graph-shaped world model, multi-model routing sophistication, full disaster-recovery automation — these get their data model and interfaces decided now so nothing in Stage 0-1 has to be ripped out, but the full implementation waits.

---

## P. Questions / Decisions Needing Your Input

1. **First real use case for Stage 1's single agent** — you've mentioned YouTube/Instagram content as a goal. Should the first working agent be the Content/Research agent, or is there a smaller, lower-stakes task you'd rather prove the system on first (since content generation touches the Approval Engine's publishing category immediately)?
2. **Initial autonomy default** — do you want JARVIS to start maximally conservative (ask before almost everything) and earn autonomy over time, or are you comfortable pre-approving certain categories (e.g., research, drafting) from day one?
3. **Budget ceiling** — a concrete monthly $ limit for API/infrastructure costs while we're in Stage 0-1, so the budget system has a real number to enforce rather than an abstract one.

Everything else in the spec's uncertainty is implementation detail I'll make strong calls on rather than bring back to you.

> **Where Stage 0 landed on these:** Q2 was already answered by the Stage
> 0 Build Brief itself (research/drafting default to `auto`, publishing/
> spending/credentials default to `ask_every_time` — seeded directly into
> the `approvals` table). Q3 was given as a range (₹3,000–4,000/month); Stage
> 0 defaults the ceiling to ₹3,500 via an environment variable, changeable
> any time without a code change (see `docs/BUDGET.md`). Q1 doesn't block
> Stage 0 (no agents exist yet) and is still open for Stage 1.
