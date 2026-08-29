# Stage 0 status

Plain-language answer to: what exists, what doesn't, what it costs, how do
I check on it, how do I stop it.

## What's live

**Stage 0 is complete and verified from the owner's own device**
(2026-08-28). Deployed on Render, signed in with Google from an iPad, one
real message answered by Gemini, and the exchange visible in the database
with the right provenance: `stated` for the owner's words, `retrieved` for
JARVIS's reply, each linked to the other. That is the Stage 0 Definition
of Done met in full.

### Long-term memory — keeping what matters

Conversation recall reaches back about ten exchanges. That is a chat
window, not a memory: tell JARVIS a birthday, talk about something else
for ten minutes, and it was gone. Measured, not assumed — after twelve
ordinary exchanges the birthday was no longer visible to the model at all.

JARVIS now keeps the parts worth keeping. After each exchange it reads
what was said and writes down anything durable — a preference, a date, a
decision, a person — as its own small memory. Later messages pull those
back **by relevance**, using Postgres' own full-text search, so a fact
from months ago returns when it is relevant, long after the conversation
it came from scrolled away.

Three things worth knowing:

- **Facts are labelled as JARVIS's own words, not yours.** A fact is a
  sentence JARVIS wrote summarising something you said, so it is stored as
  `inferred`, never `stated`, and each one links back to the verbatim
  memory it came from. The page shows them as *"JARVIS remembers"* rather
  than *"You said"*. That distinction is the provenance rule earning its
  keep: your words and JARVIS's summary of them must never become
  indistinguishable.
- **It corrects itself.** The same pass that extracts facts is shown what
  JARVIS already believes and asked what is now wrong. Saying *"actually
  it's blue"* retires the old fact instead of leaving two contradictory
  ones side by side. That is the *correct* operation from the
  architecture doc's store / recall / correct / forget — the memory system
  is now complete. Retiring is the same reversible forget used everywhere
  else, so nothing is destroyed and the old fact stays visible.
- **It costs a second model call per message.** Made *after* your reply is
  sent, so it never adds to your wait. On a free tier the cost is quota
  rather than money — roughly half as many messages per day. Switch it off
  with `MEMORY_FACTS_ENABLED=false`.

### Forgetting — taking things back

The architecture doc specifies the memory system as *store / recall /
correct / forget*. Three of those four now exist.

There are deliberately **two** different operations, because "forget that"
and "erase that" are different requests:

- **Forget** — JARVIS stops recalling it immediately. The row is still
  there, so it can be restored. This is the everyday one.
- **Delete** — the row is destroyed. For things that should never have
  been written down: a password, something private, a mistake. No undo,
  and the API says so plainly.

Plus, in bulk: **Forget everything** (reversible, one at a time) and
**Erase forgotten memories** (permanent, and it makes you type
`DELETE EVERYTHING` in full — a button on a small screen is one mis-tap
away from destroying the lot).

Every one of these is written to the audit log with who did it and when.
Deletion is recorded as `high_risk` — not because a single memory is
grave, but because destroying data should always leave a trace, including
when it was the owner doing it deliberately.

**Forgetting takes the whole exchange, not one row**, and that turned out
to matter more than it sounds. Say something private and JARVIS answers by
repeating it back — so the same words are now stored twice. Forgetting
only the half you tapped leaves the secret sitting in the other half,
while telling you it was forgotten. This was found by running it for real
against a live server: the sensitive text survived a purge inside JARVIS's
own reply. The message and its reply now travel together.

No database migration was needed: `expires_at` has been in the schema
since Stage 0 as the memory-lifecycle field, which is exactly what
forgetting is. Nothing to run by hand against the live database.

**Still missing from the doc's list: correct.** Telling JARVIS "no, my
colour is blue" and having it amend what it knows, rather than storing a
contradiction next to the original. That is the last quarter of the memory
system.

### Memory recall — the first piece of Stage 1

Stage 0 stored every exchange and then never looked at it again. Ask about
your favourite colour a message later and JARVIS would not know, even
though the answer was sitting in the database.

It now reads that back. Each message is answered with the recent
conversation in front of it, so JARVIS holds a thread — across messages,
and across days. Every reply says how many past turns it recalled, so this
is something you can watch working rather than infer from the answers
sounding right. An assistant that has quietly stopped remembering still
produces perfectly plausible replies, which is exactly why it needs to be
visible.

Three things worth knowing about how it behaves:

- **It costs money per message.** The conversation is re-sent to the model
  every time — that is how these models work, they have no memory of their
  own. So there is a hard ceiling on how much history goes (default ~2,000
  tokens' worth), and it can be lowered, raised, or switched off entirely
  without a code change. See `BUDGET.md`.
- **Only what was actually said is replayed.** Memories marked `inferred`
  or `predicted` — JARVIS's own guesses about you — are deliberately
  excluded. Feeding a guess back in as conversation is how a guess quietly
  becomes a fact, which is the precise thing the provenance field exists
  to prevent. Nothing produces those yet; the rule is in place before
  anything can.
- **It forgets the oldest first.** When the ceiling is reached, the oldest
  turns drop out. If a single message is too big for the whole budget,
  what *you* said comes back trimmed and labelled rather than vanishing —
  your words are kept in preference to JARVIS's own.

JARVIS's system prompt was updated to match: it used to be told it had no
memory recall, which is why it volunteered that in replies. It is now told
it does remember — and told, just as firmly, not to invent anything that
is not actually in front of it.

### Before that

The complete Stage 0 codebase, written and locally tested:

- A real Postgres database schema, applied and verified against an actual
  Postgres instance.
- The full JARVIS Core API, run locally and exercised end-to-end over real
  HTTP requests: sending a message, getting a reply, seeing it recorded in
  `memories` with correct provenance, seeing it logged in `audit_log`,
  reading back the budget estimate, toggling Emergency Stop and confirming
  it actually blocks requests.
- 82 automated tests, all passing (`core/tests/`).

None of that required a cloud account — it ran against a local Postgres,
with no provider key configured, so a mock adapter answered and labelled
its replies as placeholders rather than pretending. See
`core/app/llm/mock_adapter.py`. The Gemini adapter was additionally
verified against Google's live API endpoint.

**The database is real and ready.** Supabase project
`ggnyypoopkmhfgtbqync` has the full Stage 0 schema applied (confirmed by
the owner on 2026-08-26) — six tables, with the five approval defaults
seeded. Nothing is deployed against it yet, so it's sitting empty and
costing ₹0 on the free tier.

Note that I can't verify the database's contents myself: this sandbox has
no network route to Supabase (HTTPS refused by the session's egress
policy, the direct DB host is IPv6-only with no IPv6 available here, the
IPv4 pooler port times out). That's a sandbox restriction, not a problem
with your project, and no credential would change it. It means database
state is reported by you, not measured by me.

(Google's and Anthropic's APIs *are* reachable from here — that's how the
Gemini adapter was verified against a live endpoint. But I hold no
credentials for your accounts and shouldn't: a key pasted into chat is a
leaked key.)

**What's left**, in order:

| # | Step | Where | Status |
|---|---|---|---|
| 1 | Apply the database schema | Supabase SQL Editor | **Done** |
| 2 | Firebase project + Google sign-in | Firebase Console | **Done** |
| 3 | Get a Gemini API key (free) | aistudio.google.com | **Done** |
| 4 | Deploy from the repo | Render (browser only) | **Done** |
| 5 | Sign in from your iPad and send a message | Your browser | **Done** |

**Hosting: Render, not Google Cloud Run.** Cloud Run needed a command
line and a billing account (a card on file) before it would switch its
services on. Render deploys straight from this GitHub repo through a web
form — no terminal at all, which suits an iPad. `render.yaml` in the repo
root pre-fills everything except the two secrets, which Render asks for in
its dashboard and never stores in git.

The trade-off is honest: Render's free service sleeps after 15 minutes
idle and takes about a minute to wake. Cloud Run doesn't sleep, and
`infra/deploy.sh` still deploys the same container there whenever that
matters more than avoiding a card. Because JARVIS is a container talking
to a standard database, moving between them is configuration, not a
rewrite — which is exactly what the architecture doc's portability
requirement was for.

**No Google credentials are needed anywhere.** Verifying a Firebase
sign-in only requires Google's public keys, so JARVIS checks the signature
itself rather than using Firebase's Admin library. That removed a secret
from the system entirely and made the login work on any host.

**Model provider:** JARVIS supports Google Gemini and Anthropic Claude
behind a common interface, chosen with one setting (`LLM_PROVIDER`).
Gemini is the default because its free tier keeps Stage 0 at ₹0. If a
Claude key is also configured, JARVIS automatically retries with it when
Gemini fails, and the reply says which one answered. Neither key
configured is still a working system — replies are clearly-labelled
placeholders rather than a crash or a fake answer.

All five steps are done. Stage 1 can begin. `DEPLOYMENT.md` has each step
in full, should any of it ever need redoing.

### Sign-in: what went wrong, and what fixed it

Worth writing down, because it looked like three different faults and was
really one, and because the last version of it was my mistake.

Signing in on an iPad failed twice for reasons that had nothing to do with
JARVIS being wrong about who you are:

1. **Pop-up sign-in** — iOS Safari blocks pop-ups, so the window never
   opened. Nothing appeared to happen at all.
2. **Redirect sign-in** — Safari blocks the cross-domain storage Firebase
   uses to carry you back. Fixed by serving Firebase's sign-in helper from
   your own domain (`core/app/routes/auth_proxy.py`), and then made
   unnecessary by Google's in-page button, which neither pops up nor
   navigates.
3. **The sign-in was forgotten on every reload.** This one is mine. The
   sign-in was being held in a JavaScript variable — page memory, which
   the browser wipes on every reload, including the automatic one after
   each redeploy. You really had signed in; the page had simply forgotten
   by the time you sent a message, and reported it as "Not signed in yet."
   I had built the fix for this and then removed it when sign-in appeared
   to be working. Removing it was the mistake.

The fix is an ordinary login cookie. When you sign in, the server checks
the token with Google and hands your browser a small signed note saying
"this is the owner". The browser sends that note back on every request,
including after a reload. The note is readable but can't be altered — it's
signed with a secret only the server knows — and it's marked `HttpOnly`,
so no script on the page can read it either.

Two consequences worth knowing:

- **`SESSION_SECRET` must be set and kept** (`DEPLOYMENT.md` step 4g).
  Left unset, the server invents a new one on each start, and every
  redeploy signs you out — the same symptom, moved. It warns loudly at
  startup when that's the case, so it is never silent.
- **Being signed in is re-checked on every request**, not trusted for the
  cookie's whole month. So changing `OWNER_EMAIL` locks out the previous
  owner immediately rather than whenever their session happened to lapse.

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

**₹0**, as far as anything measures. Supabase, Render and Firebase are all
on free tiers. Gemini is expected to be free too — but see the caveat in
`BUDGET.md`: I could not verify `gemini-3.6-flash`'s free-tier status, so
`/v1/budget` reporting ₹0 is an assumption for that model, not a
measurement. Worth one look at Google's pricing page.

Once deployed on the free/scale-to-zero tiers named in
`DEPLOYMENT.md`, expected cost at solo, low-volume usage is
still close to ₹0 for compute/database/auth — the only real variable cost
is model API usage per message, tracked by `GET /v1/budget` once
deployed — and on Gemini's free tier that is expected to be ₹0 too. See `BUDGET.md` for exactly what that number does and doesn't
include.

## How to check if it's running

```bash
curl https://<your-render-url>/health
```

`{"status": "ok", "emergency_stop": false, "dev_mode": false}` means
JARVIS is up, accepting requests, and not in dev mode (dev mode must never
be true on a real deployment — it disables login).

Locally, the same check is `http://localhost:8080/health` after
`docker compose up -d --build`.

The test console at the root URL (`/`) also gives you a sign-in button and
a message box — the fastest way to eyeball that the whole loop works from
a browser, including your iPad's.

## How to stop it

Two levels, cheapest first:

1. **Emergency Stop** (reversible, instant, keeps everything running and
   billed as normal — it just refuses to process messages):
   ```bash
   curl -X POST https://<your-render-url>/v1/admin/emergency-stop \
     -H "Authorization: Bearer <your Google ID token>" \
     -H "Content-Type: application/json" \
     -d '{"stop": true}'
   ```
   (The test console has buttons for this too, once wired up in a later
   pass — for now it's a plain API call. `GET /health` confirms the state.)

2. **Full shutdown**: suspend or delete the service in the Render
   dashboard (your service → Settings). Your data isn't touched — it lives
   in Supabase, separately. Redeploying later from this same repo brings
   JARVIS back exactly as it was.

There is deliberately no "delete the database" step in either of these —
Stage 0 never destroys data as part of stopping the service.
