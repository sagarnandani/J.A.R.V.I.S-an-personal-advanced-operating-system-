# The Agent Foundation

What JARVIS uses to get work done by something other than itself.

It is not agents. It is the machinery every agent needs, so that adding
one is a small job rather than a new architecture each time.

## Why a modular monolith

JARVIS is one person's system on a free container. Queues, brokers and
separate services are what large teams use to let many people deploy
independently — they buy nothing here and cost real operational surface.

So everything runs in one process, with hard module boundaries. Each
piece can move out later without the pieces around it changing; the task
runtime becoming a background worker is the obvious first one.

## The parts

| Module | Its one job |
|---|---|
| `registry` | Who exists, what they may do, which version is live |
| `tasks` | Every piece of delegated work, and its dependencies |
| `orchestrator` | Objective → task graph → results |
| `runtime` | Runs one task through every layer below |
| `context` | The smallest useful briefing for one task |
| `permissions` | Least privilege, and what needs your approval |
| `model_router` | Which intelligence a job actually justifies |
| `cost` | Money, attributed to task and workflow |
| `telemetry` | What happened, replayable afterwards |
| `tools/` | Where an agent stops reasoning and touches the world |

## How a task runs

```
resolve agent → check permissions → check budget → assemble context
   → route model → invoke → charge → record result → measure
```

All of it in `runtime.py`, deliberately. One path in means there is no
second route where a permission check or a cost attribution could be
skipped, which is the failure that would make every guarantee above
merely aspirational.

## Five decisions worth knowing

**Agents ask for a tier, never a model name.** Cheap, standard or deep.
Providers rename and retire models — that has already broken this project
once — and when it happens again one mapping changes instead of every
agent. The router also returns *why* it chose, because "why did this cost
so much?" deserves a better answer than "the router decided".

**Holding a permission and needing approval are separate questions.** An
agent can hold PUBLISH and still not publish without you. Merging them
would mean the only way to require approval is to withhold the
permission — and then the agent cannot even ask.

**Two permissions are never delegated at all.** `MODIFY_CONFIG` and
`MODIFY_AGENTS`. An agent that concludes rewriting JARVIS would be useful
must not be able to act on that, however sound its reasoning.

**Context is five scopes, not one shared memory.** Working, agent,
project, shared, owner. An agent gets only what its work needs. This is a
privacy control as much as a cost one: a research agent handed your
conversation history is slower, dearer, less accurate — the instruction
gets buried — and has been shown things it had no reason to see.

**Cost is checked before spending, not reported after.** Finding out
about an overspend afterwards is an audit trail, not a budget.

## What exists now

Three mock capabilities that prove the machinery — `general.research`,
`general.analysis`, `general.writer` — and one real one:

**`research.web`** answers a question from live web sources and returns
the pages it relied on. It uses Gemini's own Google Search grounding, so
it needs no second API key and no second account.

It was built first on purpose. It holds NETWORK, spends real money, calls
a service outside our control, and can fail in ways nobody scripted —
which exercises permissions, cost, failure handling and telemetry against
reality rather than against a mock that always cooperates.

Its confidence is **computed, not asserted**: from how many independent
sources supported the answer, never above 0.85. A model asked to rate its
own certainty produces a number that sounds thoughtful and tracks
nothing. And the web agreeing is not the same as the web being right.

## Adding a capability

1. Write a module in `app/agents/capabilities/` with a `SPEC` and a
   `run(handoff, choice)` returning an `AgentResult`.
2. Declare the narrowest permissions and context scopes that will do.
3. Add it to the install list in `builtin.py`.

Nothing else. Registry, tasks, routing, permissions, cost, telemetry and
evaluation already apply to it.

## What is deliberately not built

**Autonomous planning.** `orchestrator.plan()` is the seam where it will
go. It returns steps rather than acting, so a plan can be reviewed before
anything runs. A planner that can invent arbitrary task graphs before
permissions and budgets are proven is the least safe thing to build
first.

**Approval resumption.** A task that needs your approval waits correctly
and says why; resuming it is currently manual.

**Distributed execution.** Waves run in-process. A restart mid-workflow
leaves tasks marked `running` — visible, but not yet automatically
recovered.
