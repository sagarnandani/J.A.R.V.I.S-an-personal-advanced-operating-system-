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

Three capabilities that answer from the model alone — `general.research`,
`general.analysis`, `general.writer` — and two that reach the world:

A word on the first three, because one of them caused a real failure.
`general.research` was described as "gathers and summarises what is known"
and declared the NETWORK permission. It gathers nothing: it is a single
model call with no way to reach anything. The planner reads descriptions
and permissions to decide who runs, so it picked that one for a question
about this week and answered it from training data — no sources, and
nothing on screen to say nothing had been looked up. It is now named
Recall, says plainly that it reaches nothing, and holds no permission it
does not use. A capability that overstates itself in the registry is
worse than one that does not exist.

**`research.web`** answers a question from live web sources and returns
the pages it relied on. It uses Gemini's own Google Search grounding, so
it needs no second API key and no second account.

It was built first on purpose. It holds NETWORK, spends real money, calls
a service outside our control, and can fail in ways nobody scripted —
which exercises permissions, cost, failure handling and telemetry against
reality rather than against a mock that always cooperates.

When grounding returns no sources at all, the answer itself now says so
before anything else: what follows is the model's own recollection, may
be out of date, and has not been checked. The confidence already dropped
to 0.3 in those cases, but a confidence is a number in a task row and
what the owner hears is the text — unlabelled, recollection sounds
exactly like research.

Its confidence is **computed, not asserted**: from how many independent
sources supported the answer, never above 0.85. A model asked to rate its
own certainty produces a number that sounds thoughtful and tracks
nothing. And the web agreeing is not the same as the web being right.

**`factcheck.claims`** takes claims — usually the ones a research task
just produced — splits them apart, and checks each one against live
sources on its own. It gives back a verdict per claim (supported,
contradicted, disputed, unverified), the evidence behind it, and one
overall number.

It is the second half of `research.web`, and it is what makes a
confidence figure worth reading. Research counts sources: four sources
scores 0.85 whether those four agreed, disagreed, or were four copies of
the same press release. Fact-checking asks whether the thing is actually
so.

Two rules keep it honest.

*A verdict with nothing behind it is not a verdict.* If a check comes
back with no sources, the claim is recorded as **unverified** whatever
the model said about it. A model will cheerfully answer "SUPPORTED" from
its own recollection with nothing to cite; letting that through would
launder an unchecked opinion into a green tick — worse than never
checking, because it carries a stamp.

*It is not shown what you already believe.* `factcheck.claims` holds
NETWORK and nothing else — no `READ_MEMORY`, working scope only. A
checker that knows what its owner wants to be true has been handed a
reason to agree, and the entire value of the check is that it does not
have one.

Practical limits, both deliberate: five claims per task by default
(`max_claims` raises it), because a ten-claim answer means ten searches
and on a free tier that is the difference between a check and a rate
limit; and if one lookup fails the other claims still report, while if
*every* lookup fails the task fails — five failed searches are not five
honest "unverified" verdicts, and reporting them as verdicts would look
like the claims had been examined and found wanting.

Running the pair as a chain is the ordinary use:

```python
await orchestrator.run(
    "How fast did India's economy grow?", "user:owner",
    steps=[
        orchestrator.Step("research.web", "How fast did India's economy grow?",
                          name="research"),
        orchestrator.Step("factcheck.claims", "Check the research findings",
                          name="check", after=("research",)),
    ],
)
```

## Just asking for it

The Tasks tab is where you go deliberately. Most of the time you are
already talking to JARVIS, so it can notice for itself.

While it writes your reply, JARVIS marks any message whose honest answer
needs looking up — something that depends on current information, a claim
you asked it to check, an instruction to go and find something out. The
reply comes back with a small offer under it:

> *My own figure is likely out of date, sir.*
>
> **I can look this up properly:** find the current EV subsidy in
> Karnataka and check it
> Cost: about ₹1.40, going by what runs like this have cost.
> `Go ahead`  `No thanks`

Four things about that, each ruling out a worse version:

**The mark rides on the reply already being written.** No second model
call, so no extra waiting and nothing spent classifying "good morning".

**Nothing runs without a yes.** The offer *is* the approval — the Tasks
tab exists for when a plan is worth reading first, and asking twice for
one sentence of intent is friction, not safety.

**The price is measured or absent.** It comes from the median of what
finished runs have actually cost. Before there is any history JARVIS says
it has no measurement yet, rather than inventing a figure — the same rule
that stops it inventing the income it does not track.

**The marker never reaches you.** It is stripped from the reply, from
what gets spoken aloud, and from what is written to memory. A reply
carrying `[[JARVIS_CAN_DO: …]]` would be JARVIS visibly leaking its own
machinery, and it would be replayed into every later prompt.

What the work finds is stored as a memory (`retrieved`), so tomorrow
JARVIS still knows it. That applies to Tasks-tab runs too — otherwise a
research run answers the question and is forgotten by the next message.

### By voice

Voice works too, by a different mechanism, because it has to. Live voice
talks to Gemini directly and Gemini *generates speech* — a marker in its
output is a marker read out loud, brackets and all. So the spoken path is
split in two:

- **Gemini says it in words.** It is told that it can search and check,
  though not during this spoken turn, and to say so plainly and ask
  whether to go ahead — the way a person would offer to fetch something.
- **You answer out loud.** "Yes", "go ahead", "haan karo" — JARVIS reads
  the next thing you say and starts the work. Only the opening words
  count, so a "yes" buried in an unrelated sentence does not set anything
  off, and anything that is neither yes nor no leaves the offer standing.
- **JARVIS says what it found.** Results are handed back into the spoken
  conversation, so Gemini reads them out in your language. It also says
  "looking it up now" first, because twenty seconds of silence after "yes"
  is indistinguishable from a system that has stopped working.

The card still appears, showing what was heard and carrying the sources
and the cost — detail that speech is a bad medium for — and you can press
it if you would rather. It is not the mechanism. An earlier version made
it the mechanism, and every spoken "yes" reached nothing: the work never
started, JARVIS never learned it had offered, and it asked again, and
again.

A model call per spoken turn would be real money on a free quota, so a
free keyword filter runs first and most turns never reach the model. It
reads English cues, which catches the owner's usual mixed speech because
the verb tends to arrive in English; a request made entirely in another
script will be missed. That is a known limit, not an accident.

Everything after the button is identical to the typed path: same planner,
same agents, same card, same memory.

## Adding a capability

1. Write a module in `app/agents/capabilities/` with a `SPEC` and a
   `run(handoff, choice)` returning an `AgentResult`.
2. Declare the narrowest permissions and context scopes that will do.
3. Add it to the install list in `builtin.py`.

Nothing else. Registry, tasks, routing, permissions, cost, telemetry and
evaluation already apply to it.

## Planning its own work

Until now you told JARVIS the steps. Now it can work them out: start a
workflow with an objective and no steps, and `planner` reads the registry
and decides which capabilities run and in what order.

This was built last on purpose. A planner is the one component that can
invent work nobody asked for, so the useful question is not "can it plan"
but **what can a bad plan actually do**. Four answers:

**It can only name capabilities that already exist.** The list it is
shown comes from the registry. A step naming anything else is refused —
not mapped to the nearest match, because guessing which capability was
meant is how a plan ends up doing something adjacent to what you wanted.
There is no path from "the model wrote a word" to "code ran".

**It cannot widen anything.** A plan carries a capability, an objective
and an ordering. Not permissions, not budgets, not model tiers, not
constraints — those come from the registry and the runtime, exactly as
they do for a step you wrote yourself. The runtime reads a task's
constraints when deciding which approvals apply, so a planner able to
write them could plan its way around an approval. It may not write them
at all, and what it tried is recorded.

**It is bounded before it runs.** Five steps maximum (`PLANNER_MAX_STEPS`
— a budget control: every step is a real model call). No loops. No
duplicate names. No step depending on one that isn't in the plan. Each of
those is *refused*, never quietly repaired, because a repaired plan is a
plan nobody wrote and nobody reviewed. Dropping one bad dependency would
let a checking step start before the thing it checks, find nothing, and
report success.

**It cannot fail into silence.** If the model errors, returns junk, or
proposes something that won't validate, JARVIS falls back to handing the
objective to a single agent — what it did before the planner existed —
and the reason lands in the trace. Planning must never break the request
it was helping with.

Two more things worth knowing:

*Explicit steps always win.* If you name the steps, the planner does not
run at all. You decided something on purpose.

*"Nothing here can do that" is a real answer.* Asked to email your
accountant, the planner says nothing registered can send email, and the
workflow fails with that sentence — rather than handing it to the nearest
capability and producing something confident and irrelevant.

**Read before run.** `POST /v1/plans` with an objective returns the plan
— steps, reasoning, and what was refused — and creates nothing. One cheap
call instead of a whole workflow.

## Using it — the Tasks tab

Everything above was reachable only by posting JSON, which from an iPad is
not reachable at all. The dashboard's **Tasks** tab is the door:

1. Type what you want done.
2. **Plan** — JARVIS shows the steps it proposes, why, and anything it
   asked for and was refused. One cheap call; nothing has run.
3. **Run** — the approved steps go back verbatim, so what executes is what
   you read. Re-planning here would run something nobody approved.
4. Steps appear as they finish, with confidence, cost, and — for a
   fact-check — a verdict and sources per claim.

The work runs on the server, not in the page, so a research-then-check job
survives closing the tab. The panel polls; **Recent** reopens any past run.

`GET /v1/workflows` lists recent work; `POST /v1/workflows` starts it and
returns immediately rather than holding the request open for the minute
the work takes.

`PLANNER_ENABLED=false` switches it off entirely. "Decide your own work"
is the one capability an owner should be able to withdraw without a
deploy.

## Work that happens without you

The **On a schedule** panel, on the Tasks tab. An objective and a time —
no cron syntax, because "every morning at seven" is what is meant and
cron is a thing people get wrong and then cannot debug.

This is the first code in the project that spends money with nobody
watching, and most of its design is restraint:

- **It stops at 60% of the monthly ceiling** (`SCHEDULER_BUDGET_PERCENT`).
  Unattended work must never be what leaves you unable to talk to your own
  assistant. When it pauses, it records why, so nothing stops silently.
- **Two runs a day per schedule**, adjustable. A loop is a bill nobody
  notices until the month ends.
- **Late is not skipped, but late is once.** A free-tier server sleeps, so
  a 7am job may not be looked at until 9am. Running it late is what you
  wanted; running it four times because four slots passed is not. How late
  it was is recorded, so a stale result does not read as fresh.
- **One runner at a time**, by advisory lock, because Render starts the
  replacement instance before stopping the old one.

### Making it fire on time

The process checks every minute *while awake*. On Render's free tier it
sleeps after about fifteen minutes idle, and a loop that is not running
notices nothing — so a 7am job would happen whenever you next opened
JARVIS.

`POST /v1/cron/tick` is the other half: something outside calls it and the
server wakes and runs what is due. It takes a shared key
(`X-Cron-Key` header, or `?key=` — the header is better, since query
strings end up in logs), and **with no key set the endpoint does not
exist**. An open trigger for work that spends money is not a sensible
default.

Any free pinger will do — cron-job.org, UptimeRobot, a GitHub Action.
Point it at `https://<your-jarvis>/v1/cron/tick` every 10 minutes with the
key from Render's Environment tab.

## The arrival briefing

Every line of it is now a query. It reports exchanges today, facts held,
spend against the ceiling, **tasks completed in the last 24 hours and what
they cost**, standing schedules, and anything finished since you were last
told — once, and then not again.

Money earned still says, in those words, that nothing records income.
That gap is named rather than left blank, because a model handed a prompt
with a missing figure supplies a plausible one.

The tasks line used to be a hardcoded "the task engine is not built". It
went on being sent for a week after the task engine was built, and a test
asserted that it should be. A claim about the system's own capabilities
goes stale in silence; a query cannot.

## What is deliberately not built

**Approval resumption.** A task that needs your approval waits correctly
and says why; resuming it is currently manual.

**Distributed execution.** Waves run in-process. A restart mid-workflow
leaves tasks marked `running` — visible, but not yet automatically
recovered.
