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

Money is now real too — see below.

The tasks line used to be a hardcoded "the task engine is not built". It
went on being sent for a week after the task engine was built, and a test
asserted that it should be. A claim about the system's own capabilities
goes stale in silence; a query cannot.

## Money

The third of the three things asked for on day one. JARVIS notes amounts
you state in figures — *"got ₹40,000 from the Bengaluru shoot"*, *"paid
₹5,000 for the drone battery"* — and the **Money** panel on the home
screen shows in, out and net for the month, with the recent entries.

It rides on the pass that already reads each exchange for durable facts,
so it costs no extra model call.

Two rules:

**Only a stated figure becomes a row.** No inferring an amount from
context, no rounding "a few thousand" into a number, and never a figure
JARVIS itself produced. A ledger that guesses is worse than no ledger,
because it looks like arithmetic and gets checked against a bank
statement that disagrees.

**Every row can be undone.** Forty thousand and four thousand sound
alike. Each entry has an × that deletes it outright — deleted, not
hidden as a forgotten memory would be, because a ledger that quietly
keeps a number you dropped is one whose totals you cannot check. Each
row also links back to the exact words it was read out of.

The totals are **real but partial**, and that survives into the prompt
and onto the panel: there is no bank or invoice feed, so these are only
what you have mentioned. A partial total read as a complete one is the
worst kind of wrong number, and money is where that costs most. Imports
can write to the same table later; every row records where it came from.

## Asking for it in conversation

The offer mechanism now carries two kinds of work, because it had one and
that turned out to be a hole.

**Look something up.** The original: search the live web, check claims,
through the planner. **Make a piece.** Research, verify, decide whether it
should exist, write it, review it, through the Media Director.

The second exists because of a plain failure. The owner asked for a
script in conversation. JARVIS knew `media.script` existed, because the
roster had just told it, and had no way to reach it from a conversation.
So it said it was displaying the script on screen, and nothing appeared.
A model that knows a capability exists and cannot reach it will narrate
using it. Knowing without reaching is worse than not knowing.

Two things were needed, and both are here.

**A route.** The marker the model appends now names which kind it wants,
and the offer card says which route it would take before you agree to it.
A model that forgets the prefix gets the cheaper, safer one: a look-up
spends a little and changes nothing, while a production spends more and
leaves a draft nobody asked for. The two are also priced separately from
their own history, because quoting one median for both would tell the
owner a production costs what a search costs.

Accepting a "make" goes to the Director, never through the planner. That
matters: the planner is deliberately forbidden from assembling that chain,
so routing it there would skip every gate the chain exists for.

**A rule about claiming things.** The persona prompt now says plainly that
JARVIS cannot display, open, send, post or prepare anything, that the
offer is the only way work starts, and that until it is accepted nothing
has happened. Reporting an action it did not take is the same class of
error as inventing a figure, and it needed saying just as explicitly.

Nothing is shown in the conversation itself. The finished piece lives on
the Media tab, and the chat answer carries a link that opens it there.

## Not everything should go through the model

A week of "it says it is working and there is no script" had two causes,
and both come from the same mistake: making the conversational model the
only way to start work.

**By voice it was impossible.** The spoken path decides what a turn meant
in two stages, and both only knew about looking things up. A free keyword
filter ran first — "look up", "find out", "search", "latest" — and a
spoken "write me a script about X" contains none of those, so it was
dropped before any model saw it. Even past the filter, the classifier
asked one question ("does this need looking something up?") and the
acceptance path assumed the answer. The owner talks to JARVIS far more
than he types at it, so the route he was actually using could not start a
production at all, while the speaking model, which had just been told the
media chain exists, described it working. Both stages now carry the kind,
and a spoken yes to "shall I put that together" runs the Director.

**By text it was three things that could each quietly fail.** The model
had to notice the request, mark it with the right kind, and have the
offer survive the registry check. Each is a place where nothing happens
and nobody is told.

So there is now a route that asks the model nothing at all: a topic, a
brand and a **Make** button on the Media tab. It cannot be defeated by a
prompt that grew too long, by an instruction buried under a page of
roster, or by a model that decided to be helpful and answer instead. When
it fails it fails visibly, with a reason, in the same second.

The lesson is worth keeping: a language model is a good way to *offer* to
start work and a poor one to *guarantee* it. Anything the owner must be
able to rely on needs a path that does not depend on what the model chose
to say.

## Right now

One query, one answer, used by both the screen and the conversation.

It exists because the same question kept being unanswerable. The owner
asked for a script, was told the Media Director was working on it, and
there was no script — and neither of us could tell whether nothing had
ever started or something had started and stalled. Every fix before this
one was a guess at which, and each addressed a different half of a thing
nobody could see.

`app/activity.py` answers it: what is running, what is queued, what is
being made and how far it got, how long each has been that way, and
whether that is long enough to call it stuck rather than slow. Two
answers that look alike and are not: **nothing is running** and **nothing
has ever been started**. Only the second says the request never got
through.

The **Right now** panel puts that on the home screen, above the fold,
where the conversation already is. It is not the Activity feed that was
removed — that listed finished work after the fact and told the owner
nothing they had not just watched. This shows only work in flight, and it
hides itself completely when there is none, because a panel that says
"nothing" all day is one you stop reading.

The conversation gets the same figures. A reply that mentions the
machinery has the true state attached underneath it, so a claim and the
fact that contradicts it sit one line apart.

### Why a fact, not just a guard

The guard that catches false claims had become whack-a-mole. It caught
the first person, so the model used the third. It caught "in progress",
so the model said the Media Director was working on it. Each round of
patterns bought a few days.

What every one of those sentences had in common is that it **names a part
of the system and says that part is doing something**, so the guard now
looks for those two signals together rather than for phrases. Naming the
Media Director is fine on its own — the owner asks what agents exist — and
so is the word "working".

But the stronger half is the fact rather than the accusation. Deciding
whether a sentence is a lie needs a judgement the server cannot make
reliably. Stating what is actually running needs no judgement, is never
wrong, and covers the phrasings no pattern will ever reach. So the
correction stays strict, requiring both signals, and the state line
attaches on the mention alone.

### Stalled work no longer waits for a deploy

Recovery ran only at startup, so a run that died mid-flight stayed stuck
until the next deploy. The scheduler tick now picks up stalled work every
minute as well. The fifteen-minute threshold inside it is far longer than
any step legitimately takes, so it cannot interrupt work that is really
running.

## Saying it did something it did not do

Three reports in one week, all the same failure. JARVIS said it was
displaying a script on screen. It said a piece was waiting on the Media
tab. It said it was fetching the latest information, then answered from
training data that had never heard of the thing being asked about.

Each time the model knew a capability existed, could not reach it from a
conversation, and described using it. The prompt was told not to. That
helped and was not enough, for two reasons worth writing down.

**A rule about finished actions does not cover the present continuous.**
The first version said not to claim it had shown, sent or generated
anything. The model switched tense. "I'm fetching the latest" and "give
me a minute" are the same lie, and both reached the owner.

**A prompt is advice, not a mechanism.** So `app/claims.py` checks the
reply before it reaches the owner and adds one honest sentence after a
claim nothing backs. Two rules keep the guard from becoming its own
problem: it only ever **adds**, never rewriting or deleting the model's
words, and it only fires when **no offer accompanies the reply** — with a
card on screen, "I'll look that up" is a true statement about a button
the owner is looking at.

The patterns are deliberately narrow. A guard that fires on ordinary
replies gets ignored, and then it is worse than nothing, so the tests
check the sentences that must be caught and an equal number that must be
left alone.

The first version of that list caught the first person, so the model
moved to the third. Asked why a script was slow, it said the work was in
process and that some topics take longer to research. Neither sentence
contains the word "I", and the second is the worst kind: an explanation
invented for a delay that does not exist.

The guard also stays quiet when something genuinely is running. Once the
briefing carries work in flight — how many steps done, which one it is
on, how long ago it started — "it is still being researched" is a report
rather than a fabrication, and correcting it would make the guard the one
telling the untruth. Supplying the fact matters more than forbidding the
invention: JARVIS had no way to see work in progress at all, which is why
the one question it was being asked was the one thing it could not
answer.

**A dropped offer is no longer silent.** When the model marks work and
the registry cannot take it, that used to be a server log line and
nothing else: the owner read a sentence saying work was coming, saw no
card, and had nothing on screen saying why. It is now said in the reply,
and for a media request it says explicitly that the Media tab will stay
empty.

**It does not draft the content itself either.** Asked for a script, it
wrote one in the chat window. A draft written there has had no research,
nothing verified, no citations and nobody reviewing it, which is the
whole reason the media chain exists — so writing one is not a helpful
shortcut, it is the failure the chain was built to prevent.

### An accent is not a language

The owner speaks English with an Indian accent, in Karnataka, and the
spoken path started answering him in Kannada before a search. Two things
caused it. The standing instruction said to answer in the language he
speaks, and the injection sent at the moment of acknowledgement asked for
a fresh language choice at exactly the point the model was least certain.

Both now say the same thing: switch only when the **words** were in
another language, never from an accent, a location, or an earlier
conversation. When uncertain, English.

## Saying yes to something that is waiting

A task that needs approval stops at `waiting_approval` and says what it
wants and why. Answering it writes a row in `task_approvals` — which task,
which category, who decided, and **what they were shown when they
decided**. That last field is the point: an approval history is only
evidence if it records the thing that was approved, rather than merely
that something was.

Approving re-queues the task and lets the workflow carry on. It does not
run anything itself, and it does not approve a *category* — the decision
is recorded against that one task and no other, so "yes to this" never
quietly becomes "yes to these from now on". Rejecting cancels the task
and everything depending on it, as **cancelled**, not failed: nothing
went wrong, you decided against it, and a rejection rate says something
about the work where a failure rate says something about the system.

The same rows are what a later, more autonomous stage would have to stand
on. "This workflow has passed twenty times unedited" is a query over
them.

## The Media Company

Four capabilities and a recipe, on the same foundation as everything
else. Nothing here is a second engine: the media agents run through
`runtime.run_task` like any other capability, which is why permissions,
budgets, cost attribution and telemetry hold for them without being
re-implemented.

**`media.scout`** looks for developments worth making something about and
returns a short ranked queue with a reason for each. Ranking is the
product: anyone can list what happened today, and the value is in saying
which two are worth spending research money on. Saturation counts
*against* a story, so a heavily covered one needs a much better angle to
earn the same money as a fresh one. An empty queue is a correct answer.

**`media.strategy`** decides whether researched material should become
content at all. Its most valuable answer is no, and the whole reason it
is a separate agent from the writer is that a writer asked whether to
write always says yes. By the time it runs, the research has already been
paid for — which is exactly the pressure that makes a system publish weak
work, and how a content spam factory starts. A decline ends the workflow
*cleanly*, recorded as a decision rather than a failure.

**`media.script`** writes the piece, in the voice of whichever of the two
brands the strategy chose, and every factual line carries the claim it
rests on. That citation requirement is also the anti-plagiarism
mechanism: a script built line by line from verified claims cannot be a
lightly rewritten transcript. Its confidence falls with each line it
could not tie to the research, so weak work arrives at review already
flagged.

**`media.review`** is the gate, and it is the only agent in the system
with **no permissions at all** — no network, no memory, nothing. Give it
the network and it starts researching instead of judging; give it memory
and it starts agreeing with the house view. It is also the only
capability routed to the deep tier first, because a weak gate is worse
than no gate: it produces a stamp.

The reviewer **fails closed**, in three ways that all came from asking
what a bad day looks like. A verdict it cannot read is a *revise*, not a
pass. A "pass" that also lists things that must be fixed is a revise. A
"pass" that lists claims the evidence does not support is a revise. Left
open, each of those is a route by which weak work acquires approval.

### The two brands

`app/media/brands.py` holds both voices as data, and both the writer and
the reviewer read the same copy. If each held its own, the reviewer would
drift from the writer and start rejecting work for breaking a rule the
writer never had.

### The gates

`media.director` decides what graph to run and what its results mean. It
commands no agents — the orchestrator does that — so there is still
exactly one path by which an agent executes.

Four outcomes, and only one of them is "ready":

- **stopped** — verification contradicted a claim the piece would have
  rested on. This one stops the run *mid-flight*, through a gate the
  orchestrator asks between waves, so the strategy, script and review
  never happen. Checking afterwards would have been a label rather than a
  gate: by then the script exists and has been paid for.
- **declined** — the strategist decided against it. Recorded as a
  completed workflow, because it is a decision, not a fault.
- **rejected** / **needs_you** — editorial refused it, or a single
  revision still did not fix it. The revision loop is bounded at one: a
  reviewer and a writer left alone will argue until the budget is gone.
- **ready** — passed review, and waiting for you.

**Nothing in the chain can publish.** No capability holds `PUBLISH`, the
furthest anything reaches on its own is `ready`, and the only thing that
moves a piece past that is your finger on a button. Approving records
that you said yes; it sends nothing anywhere.

### The content record

One row per piece in `content_pieces`, written when production starts and
updated when it settles. The workflow stays the record of what happened;
this is the record of what exists. Written *before* the first model call
on purpose: a run that dies halfway leaves a piece stuck at `producing`,
which somebody can see, rather than nothing at all, which nobody can.

Each row carries **two cost figures that are never added together**.
`spend_inr` is money that was actually billed. `shadow_inr` is what the
same work would have cost on a paid equivalent. On the free tier the
first is zero and true, and the second is the only figure that makes two
pieces comparable — so the panel shows both, side by side, and calls the
second one what it is.

### Using it — the Media tab

Three columns: what might be worth making, what is being made, and
whatever is waiting on you. A scan looks and ranks and writes nothing.
Tapping an opportunity starts a production, which runs five agents and
several minutes behind the request, so the tab polls rather than holding
the connection open.

## JARVIS knowing what JARVIS is

Asked whether it knew about the agents built for it, JARVIS said no. It
was right to: nothing had ever told it. The briefing gave it its own
spending, its own schedules and its own finished work, and said nothing
at all about its own capabilities — so it answered the question from the
model's training data, which has of course never heard of any of this.

So the briefing now carries a roster, generated from the registry on
every message, exactly like every other measured figure in it. It names
each active capability and what it is for, and it makes three
distinctions that matter:

- **What it can start from a conversation, and what it cannot.** A
  capability that reports to a supervisor runs as a step of that
  supervisor's chain, started from the Media tab. Saying "I'll make you a
  video" when the chain is started from a tab would be a promise it
  cannot keep.
- **Having a capability is not having used it.** The roster says so in
  words, because a list of what exists read as a list of what has
  happened is the same class of error as an invented figure.
- **What nothing can do.** "No registered capability holds the publish
  permission" is computed, not asserted, so it stops being said the
  moment it stops being true.

### The gate this closed

The same failure had a second half. `media.script` is registered with the
writing task type, so a plan for "write me a script about X" could name
it on its own: no research, no verification, no editorial review, and an
uncited script at the end of it — every gate that chain exists for,
skipped by a plan that looked perfectly reasonable.

The planner's catalogue now excludes any capability that reports to a
supervisor. Chains are started by their coordinator with explicit steps,
and explicit steps never go through the planner, so nothing is lost.

## Seeing the organisation — the Agents tab

One page that answers three questions at a glance: what agents exist, who
reports to whom, and what each one does. One click further: is it
healthy, what is it doing, what can it reach, what is it costing, and how
has it been going.

**Nothing on that page is maintained by hand.** There is no list of
agents in the HTML and no drawn diagram. `/v1/org` reads the registry,
the task rows and the metrics, and the page renders whatever comes back —
so an agent registered, reassigned, degraded or retired appears, moves or
changes colour on the next load. A chart somebody has to remember to
update is a chart that is wrong within a month, and wrong quietly.

Two nodes are not agents, and the page says so rather than pretending:

- **JARVIS** is the orchestrator. It plans, routes and settles, holds no
  capability, and is not in the registry.
- **A supervisor nobody registered** — `media.director`, say — is a
  recipe and a set of gates. It appears so the reporting line is visible,
  its panel opens a domain summary rather than agent metrics, and the
  first line of that panel is "Not an agent".

Inventing registry rows for either would put things in the registry that
cannot run, and the registry is what the runtime routes on.

**An indented tree, not a box-and-line graph.** A drawn org chart looks
impressive at nine agents and is an unreadable tangle at ninety: it needs
pan and zoom on a phone and shrinks text until it cannot be read. An
indented tree collapses, searches and scrolls, and reads the same at any
size. Above twenty agents it opens collapsed to the first level, and a
search reveals matches inside shut branches rather than merely filtering
what is already open.

**Blanks where nothing is measured.** Success rate, latency, confidence,
failures and refusals are all recorded on every run, so those are real.
Correction rate and quality score are not recorded anywhere, so the panel
names them as unmeasured instead of showing a zero. And an agent that has
run twice is reported as unproven rather than given a success rate — two
runs and one failure is two runs, not a 50% failure rate, and reporting
it as a rate would have you retiring a capability over a bad afternoon.

**Both cost figures, never added.** Billed today, billed over thirty
days, at paid rates over thirty days, and per task. Same rule as
everywhere else.

**There is no create-agent button, on purpose.** A new capability is
written, reviewed, registered as experimental and only then activated.
Agents are not made in production by tapping something.

## The voice

Two paths, and only one of them can take a voice profile. This is the
first thing to know, because the difference decides everything else.

**Gemini Live** is the spoken conversation. Audio goes up, audio comes
back, and the model produces the speech itself. There is no synthesis
step in the middle, so there is nothing to configure: its voice is one of
Gemini's presets and `JARVIS_VOICE_V1` does not reach it. What it buys in
exchange is latency and mixed-language handling that a
dictate-then-answer-then-speak pipeline does not match.

**A typed reply read aloud** goes text first, then speech. That path
takes the profile, and it is where the voice module lives.

### JARVIS_VOICE_V1

`app/voice/profile.py` holds every voice setting. Before it, the voice was
decided in four places at once: a preset in config, a regular expression
matching voice names in the browser, and a hard-coded rate and pitch a few
lines below that. Changing how JARVIS sounded meant finding all four.

The profile is written in the owner's terms, not any provider's: British,
forty, medium-low, around 140 words per minute, controlled, subtly dry.
Each engine translates that into whatever knobs it actually has, and the
translation lives in the engine, which is what makes a provider swap a new
file rather than an edit to everything.

It also carries what the voice must **not** sound like. That half of a
voice brief is the one most easily lost, and it is what stops the thing
drifting into a movie trailer.

Six deliveries, all small departures from ordinary conversation:
`normal`, `analysis`, `important`, `warning`, `success`, `humour`. Large
departures are how a controlled voice starts acting, so the tests bound
them.

### The engine seam

`app/voice/engine.py` is the seam. Nothing above it knows which engine is
speaking, the same way nothing knows which model answered. `select()`
takes the configured engine, falls back to whatever is available, and
falls back finally to the browser, which is last on purpose because it is
the one that always works. An owner whose neural voice has fallen over
should hear a plainer JARVIS, not silence.

One engine exists today. `browser` is the device's own synthesiser: no
key, no cost, works offline, and it **cannot sound like the brief asks**.
Those are Apple's and Google's system voices. The profile gets the accent,
the pace and the delivery right, and the timbre is as good as the device.
A server-side neural engine is a new module plus one setting, and nothing
above it changes.

### What is actually heard

The old path handed the whole reply over as one utterance and cut it at
800 characters, so a long answer was silently truncated mid-sentence. Now
the reply is split into sentences and queued, so JARVIS begins on the
first while the rest wait, nothing is dropped, and stopping is immediate
because only one sentence is ever in flight.

The splitter is careful about the full stops that are not sentence ends.
`Rs.1,200`, `Dr. Rao`, `3.5 percent`. A wrong split is not a subtle bug:
the voice stops mid-thought and starts again, and it sounds like a fault.

One honest limit, marked as such in the code. A rate of 1.0 means a
different number of words per minute on every voice and platform, so the
server can only estimate the rate that hits 140. The page measures it: it
knows how many words it sent and times how long they took, and after the
first real utterance the estimate is replaced by an observation, kept per
device because it is a property of the device.

## What is deliberately not built

**Publishing.** Nothing connects to YouTube, Instagram or anywhere else.
A piece that is approved is approved, and that is where the system stops.

**Asset generation.** No thumbnails, voice-over or video. The script
names a thumbnail concept; making it is still yours.

**Autonomous publishing (Stage 2 and 3).** The approval history in
`task_approvals` and the outcome of every piece in `content_pieces` are
the evidence a later stage would have to stand on. Without that record it
would be a switch somebody flips on faith, which is the version of this
that ends in a correction to publish.

**Distributed execution.** Waves run in-process, so a workflow belongs to
whichever instance started it.

Work interrupted by a restart *is* recovered now, and it took two goes to
get right. A task is marked `running` before the model call and
`completed` after, so a deploy or a crash in between used to leave it
running for ever.

The first fix re-queued it. That was half the job and it made the symptom
quieter rather than fixing it: nothing in the system advances a queued
task on its own, so the work moved from running for ever to queued for
ever. From outside it was identical — a script asked for, said to be in
progress, and still not there ten minutes later.

So `app/resume.py` does both halves at startup. Re-queue the task, then
actually run the workflow it belongs to. A media workflow goes back
through the Director rather than the orchestrator, because advancing the
graph alone would finish the steps and leave the content record at
`producing` for ever: the gates and the outcome live in the Director's
settle step.

Two bounds, because this spends real money without being asked. Only work
touched in the last six hours is resumed, and only five at a time. Older
work is marked failed with a reason — a workflow from three days ago
quietly starting up on a restart is a worse surprise than one that says
it was interrupted.
