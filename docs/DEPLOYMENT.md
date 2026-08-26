# Deployment checklist

These are steps you run yourself. I can't run them for me-side, and it's
worth being precise about why, because it changes what's worth sending me:

**This build session has no network route to your infrastructure.** I
tested against your real Supabase project and hit a hard block in three
independent ways — outbound HTTPS to `*.supabase.co` is refused by this
session's egress policy (403), the direct database host is IPv6-only and
this sandbox has no IPv6, and the IPv4 pooler port times out. So sending
me a database password or API key wouldn't unblock anything; it would just
put a secret in a chat transcript for no benefit. Don't.

Everything below is therefore written to be done from a browser wherever
possible — no terminal, no local Python — since your primary device is an
iPad.

Nothing here costs money at Stage 0 usage levels if you stick to the
tiers named below — see `BUDGET.md`.

## 1. Database — Supabase (free tier)

**Your project:** `ggnyypoopkmhfgtbqync` — already created, nothing to set
up here. Dashboard: https://supabase.com/dashboard/project/ggnyypoopkmhfgtbqync

### 1a. Apply the schema (do this from any browser, iPad included)

You do **not** need Python, a terminal, or this repo on your device.

1. Open the SQL Editor:
   https://supabase.com/dashboard/project/ggnyypoopkmhfgtbqync/sql/new
2. Open `db/manual_setup.sql` from this repo on GitHub, click the "Copy raw
   file" button, and paste the whole thing into the editor.
3. Press **Run**.

You should see a success message. To confirm it worked, open the Table
Editor — you should now have six tables: `memories`, `tasks`, `audit_log`,
`approvals`, `system_control`, and `schema_migrations`. Click `approvals`
and you should see your five approval defaults already filled in
(research/drafting → `auto`, publishing/spending/credentials →
`ask_every_time`).

That file is safe to run twice — every statement in it is written to skip
work that's already been done, and the whole thing runs as a single
transaction, so a failure partway through leaves the database untouched
rather than half-built.

(The alternative, if you ever do have a terminal handy:
`DATABASE_URL="<your connection string>" python3 db/migrate.py`. Both
paths record the same bookkeeping, so you can freely switch between them
without anything being applied twice.)

### 1b. Get the connection string for the deployed service

Open Settings → Database → **Connection string**, and pick the
**Session pooler** tab (NOT "Direct connection"). It looks like:

```
postgresql://postgres.ggnyypoopkmhfgtbqync:[YOUR-PASSWORD]@aws-0-<region>.pooler.supabase.com:5432/postgres
```

Replace `[YOUR-PASSWORD]` with your database password (Settings →
Database → Reset database password if you don't have it). That whole
string is your `DATABASE_URL`.

**Why the pooler and not the direct connection** — this is a correction to
what I originally wrote here, and I found it by actually testing against
your project rather than assuming:

- Supabase's direct database host (`db.ggnyypoopkmhfgtbqync.supabase.co`)
  now resolves to an **IPv6-only** address. I confirmed this on your
  project specifically.
- Google Cloud Run's outbound networking is IPv4. So a direct connection
  string would deploy fine and then fail to reach the database at runtime
  — the most annoying category of bug, because everything *looks* correct.
- The pooler hosts are reachable over IPv4, which sidesteps it entirely.
  Supabase also charges extra for an IPv4 add-on on direct connections;
  the pooler is free.

If you accidentally grab the **Transaction pooler** string (port `6543`)
instead of the Session pooler (port `5432`), it still works — the code
detects that port and adjusts how it talks to the database automatically
(`core/app/db.py`). Either is fine; session pooler is marginally faster.

## 2. Auth — Firebase

This is what stops anyone else who finds your JARVIS URL from using it.
All of it is browser work — fine from an iPad.

### 2a. Create the project

1. Go to https://console.firebase.google.com and click **Create a project**.
   Name it whatever you like (`jarvis` is fine).
2. It'll offer Google Analytics — **turn it off**. You don't need it, and
   it's one less thing collecting data.

### 2b. Turn on Google sign-in

1. In the left sidebar: **Build → Authentication → Get started**.
2. Under **Sign-in method**, click **Google**, toggle it **Enable**.
3. Pick a support email (your own), then **Save**.

Google sign-in is the right choice here over email/password: no password
to type on an iPad keyboard, and Google guarantees the email address is
verified, which the next step relies on.

### 2c. Register a web app

1. Click the **gear icon → Project settings**.
2. Scroll to **Your apps**, click the **web icon** (`</>`).
3. Give it a nickname (`jarvis-web`), **don't** tick Firebase Hosting,
   click **Register app**.
4. Firebase shows you a code block. You need exactly four values from it:
   `apiKey`, `authDomain`, `projectId`, `appId`.

These are public identifiers that ship inside any web page using Firebase,
not credentials — they name your project the way a street address names a
house. What actually protects JARVIS is the owner check in 2e.

**Already done for your project.** These values are baked into
`infra/deploy.sh`, so there's nothing to copy:

| | |
|---|---|
| `projectId` | `jarvis-by-claude-a1026` |
| `authDomain` | `jarvis-by-claude-a1026.firebaseapp.com` |
| `apiKey` | `AIzaSyCezIYP0fAG-Yq55Z43W4qdwLXEKzC4g1M` |
| `appId` | `1:701562415519:web:1d377f036cd62c4c66b5aa` |

(`storageBucket` and `messagingSenderId` were in the block you copied but
aren't needed — JARVIS uses Firebase only for sign-in, not storage or
messaging.)

### 2d. Service account key — you can skip this

Firebase's *Project settings → Service accounts* tab offers a **Generate
new private key** button. **You don't need it, and you shouldn't download
it.**

A Firebase project *is* a Google Cloud project. Yours is
`jarvis-by-claude-a1026`, and that's where JARVIS will be deployed. When
a service runs on Cloud Run inside the same project, Google hands it the
right credentials automatically — no key file, nothing to store, nothing
that can leak. The deploy script relies on this.

(If you ever run JARVIS somewhere outside Google Cloud — a Mac mini, say —
you'd need that key then. Not now.)

### 2e. Say who the owner is

Set `OWNER_EMAIL` to the Google address you'll sign in with — for you,
`sagarnandani99@gmail.com`. That's it.

Only that address gets in. Everyone else who signs in gets a clear "this
JARVIS is configured for a single owner and this account is not it."

Two details worth knowing:

- The check only accepts an email Firebase reports as **verified**.
  Without that, someone could register an unverified account claiming your
  address and be let straight in. Google sign-in always reports verified,
  so this is invisible to you — it just closes the hole.
- There's also an `OWNER_UID` setting (Firebase's internal user ID). It's
  more precise, but you can only look it up *after* signing in at least
  once — which would mean deploying, signing in, copying the ID, and
  deploying again. Using your email avoids that entirely. You can set
  `OWNER_UID` later as well if you ever change email address; either one
  matching lets you in.

If neither is set, JARVIS refuses every request with an error saying so,
rather than defaulting to letting anyone in.

## 3. Anthropic API key

This is what lets JARVIS actually think. Without it the system runs but
replies with an obvious placeholder saying no model was called.

1. Go to https://console.anthropic.com → **API keys** → *Create key*.
2. Copy it (starts with `sk-ant-`). You can only see it once.
3. **Don't paste it here in chat** — the deploy script in step 4 asks for
   it directly and puts it straight into Google Secret Manager.

Also worth doing while you're there: set a **spend limit** on the Anthropic
account itself (Settings → Limits). JARVIS tracks its own estimated spend
and warns you, but a hard cap at the source is the one guardrail that
can't be undone by a bug in my code. ₹3,500/month is roughly $40 — see
`BUDGET.md`.

## 4. Deploy — Google Cloud Run

Deploying needs a command line, which an iPad doesn't have. **Google Cloud
Shell** solves this: it's a full terminal that runs in a browser tab, free,
with the Google Cloud tools already installed.

### 4a. Open Cloud Shell and get the code

1. Go to https://console.cloud.google.com/?cloudshell=true and make sure
   the project selector at the top says **jarvis-by-claude-a1026**.
2. Wait for the terminal to appear at the bottom, then run:

```bash
git clone -b claude/new-session-v0jp79 \
  https://github.com/sagarnandani/J.A.R.V.I.S-an-personal-advanced-operating-system-.git jarvis
cd jarvis
```

### 4b. Run the deploy

```bash
bash infra/deploy.sh
```

It will ask you for two things — your Supabase connection string (step 1b)
and your Anthropic API key (step 3). Typing is hidden. Both go straight
into Google Secret Manager and are never written to the repo, the
container image, or the deployment logs.

Everything else is already filled in: your Firebase details, your email as
the owner, the budget ceiling, the region (Mumbai). The first run takes a
few minutes, mostly building the container.

It's safe to run again any time — that's also how you deploy future
changes. It skips secrets that already exist and won't ask twice.

When it finishes it prints your JARVIS URL. It looks like
`https://jarvis-core-<random>-el.a.run.app`.

### 4c. Tell Firebase to trust that URL

**Sign-in will fail without this step**, with a confusing
"unauthorized domain" error.

1. Open https://console.firebase.google.com/project/jarvis-by-claude-a1026/authentication/settings
2. Under **Authorised domains**, click **Add domain**.
3. Paste the *host part* of your JARVIS URL — the middle bit only, no
   `https://` and no trailing slash. For example, if the URL is
   `https://jarvis-core-abc123-el.a.run.app`, add
   `jarvis-core-abc123-el.a.run.app`.

Firebase only allows sign-ins that originate from a domain on this list —
a sensible protection, but it can't know about your new URL until you add
it.

**Send me the URL** once you have it and I'll walk you through step 5.

## 5. Verify the full loop

1. Open the deployed URL in a browser (iPad included).
2. Sign in with Google.
3. Send a message, confirm you get a reply.
4. Click "Load recent memories" and "Load recent audit log" — confirm the
   exchange shows up with `origin: stated` for your message and
   `origin: retrieved` for JARVIS's reply.
5. Click "Load budget status" — confirm it shows a small non-zero spend
   after step 3.

That's the Stage 0 Definition of Done's "one real request/response
round-trip works end-to-end and is visible in memories and audit_log with
correct provenance" — confirmed from your own device, which is the actual
sign-off condition, not just this doc.
