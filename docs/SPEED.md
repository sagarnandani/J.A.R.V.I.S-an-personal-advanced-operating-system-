# Why JARVIS feels slow, and what actually helps

Written because "it's slow" is one word covering four different problems
with four different fixes, and three of them are not in the code.

Every reply now tells you where its time went:

> *(Took 3.4s -- 3.1s waiting for the model, 0.3s for JARVIS's own work.)*

Read that line first. It tells you which of the following you are looking
at, so you fix the right thing.

## 1. The first message after a break — about a minute

**This is almost certainly what you noticed.** Render's free plan puts a
service to sleep after 15 minutes with no traffic. The next request has to
start the container from cold before anything else happens.

No amount of code makes this faster. It is the free plan, working as
advertised. Three ways out:

| Option | Cost | Effect |
|---|---|---|
| Live with it | ₹0 | Instant while you're using it, ~1 min after a gap |
| Render Starter plan | ~₹600/mo | Never sleeps. One setting, no work |
| Your own server | Electricity | Never sleeps. Real work — see below |

₹600/month sits comfortably inside the ₹3,500 ceiling and is by far the
smallest amount of effort per second saved.

You can also keep the free service awake by pinging `/health` every 10
minutes from a free scheduler. It works, but a free service that never
sleeps is a free service being used as a paid one, and Render's monthly
free hours are roughly one service running continuously — so there is no
headroom left for anything else.

## 2. Waiting for the model — a few seconds

This is `model_ms`, and after the first message it is usually most of the
time.

The lever here is **thinking**. Gemini's models reason to themselves
before answering. You never see that reasoning, it is billed as output
tokens, and it is time you spend watching a spinner. For holding a
conversation it buys very little.

`GEMINI_THINKING_BUDGET` controls it:

- `-1` — send no setting; the model decides. **The default.**
- `0` — off. Faster and cheaper, on models that allow it.
- a number — a token allowance, for when answers genuinely need working out.

**Honest note: this lever did not work here.** Setting it to `0` looked
like the biggest single speed win available, and `gemini-3.6-flash`
refused it outright — a `400 INVALID_ARGUMENT` that broke every message
until it was reverted. Hence the `-1` default: a value a known model
rejects has no business being the default, because it would waste a
failed call on every restart.

It is still worth trying on a different model, and it is now safe to try:
if the model refuses, JARVIS retries without the setting and carries on
instead of failing your message.

The rest of `model_ms` is Google's own speed and the round trip to their
servers. Nothing on your side changes that.

## 3. JARVIS's own work — should be well under a second

This is `our_ms`: reading your conversation back and writing the new
exchange down.

It used to be eight separate trips to the database, one after another,
and five of them happened *after* the answer already existed — the owner
watching a spinner while JARVIS filed paperwork. It is now two:

- the stop-flag check and the memory recall go at the same time, since
  neither depends on the other;
- the message and reply are written in a single statement (both IDs are
  generated up front so each row can name the other), at the same time as
  the audit row.

If `our_ms` is above about half a second, the likely cause is distance:
your Render service is in Singapore, and if your Supabase project is in a
distant region every one of those trips crosses the planet. Both being in
the same region is worth more than any code change.

## 4. Long conversations

Memory recall re-sends the conversation with every message, so a longer
history means more for the model to read before it starts answering.
`MEMORY_RECALL_MAX_CHARS` caps this (default 8000, roughly 2,000 tokens),
which is why this cost does not grow forever. Lowering it makes replies
slightly faster and cheaper, at the cost of a shorter memory.

## Would my own server be faster?

**For the waiting-a-minute problem: yes, completely.** A machine at home
does not sleep. That is the single biggest win available, and it is real.

**For everything else: no, and it can be worse.** The model call is the
majority of a warm reply, and that is Google answering over the internet
— identical whether the request leaves a data centre or your living room.
If anything a home connection adds a little latency. Running a model
locally instead would need serious hardware, and at that price would be
slower *and* noticeably less capable than Gemini.

The honest comparison:

| | Render free | Render Starter | Home server |
|---|---|---|---|
| Sleeps | Yes (~1 min wake) | No | No |
| Monthly cost | ₹0 | ~₹600 | Electricity |
| HTTPS, domain, certificates | Done | Done | Yours to set up and renew |
| Reachable from outside home | n/a | n/a | Needs a tunnel or port forwarding |
| If your internet or power drops | n/a | n/a | JARVIS is down |
| Your data | Someone else's disk | Someone else's disk | Yours |
| Work to maintain | None | None | Ongoing |

A home server is the right answer for one reason above all others:
**wanting your own data on your own hardware.** That is a good reason, and
it is in the spirit of the whole project. Speed alone does not justify it,
because the part that is slow is not the part you would be moving.

If you do go that way, nothing needs rewriting — JARVIS is a container
talking to a standard Postgres. Point it at a local database and run it.
That portability was the reason for refusing to depend on any Google-only
service back in Stage 0.

**My suggestion:** try it warm first. Send a message, then straight away
send another. If the second one is quick, what you were feeling was the
sleep, and ₹600/month solves it with no work at all.

---

## Voice, and why it sounds like it does

JARVIS speaks using the browser's own speech engine (`speechSynthesis`).
No API, no key, no cost, no quota, works offline. It reads replies in
whatever language they are written in, if a voice for that language is
installed — on an iPad, **Settings → Accessibility → Spoken Content →
Voices** adds more, and the Siri voices there are much better than the
default.

**It is not the voice you heard in AI Studio.** That is Gemini's own
audio generation — the model produces speech directly rather than a
device reading text, which is why it sounds natural and handles Indian
languages properly. It is a different API, it costs money per character
or per second, and it has quotas. Wiring it in is a real option, and an
honest comparison first:

| | Browser voice (now) | Gemini native audio |
|---|---|---|
| Quality | Fine to robotic, depends on installed voices | Natural |
| Cost | ₹0 | Charged per use, counts against the budget |
| Works offline | Yes | No |
| Indian languages | Only if that voice is installed | Handled properly |
| Latency | Instant | Another round trip after the reply |

If the free voice is not good enough, that is the upgrade — but it should
be a deliberate decision against the ₹3,500 ceiling, not a default.
