# Deployment checklist

These are steps you run yourself. I can't run them for me-side, and it's
worth being precise about why, because it changes what's worth sending me:

**I can't reach your infrastructure, and shouldn't hold its keys.** Two
separate limits, stated precisely:

- **Supabase is blocked outright** from this build session. Outbound HTTPS
  to `*.supabase.co` is refused by the session's egress policy (403), the
  direct database host is IPv6-only and this sandbox has no IPv6, and the
  IPv4 pooler port times out. No credential changes that.
- **Google Cloud and the model APIs are reachable** — I verified the Gemini
  adapter against Google's live API and got a proper response back. But I
  have no credentials for your Google account, and you shouldn't give me
  any: a key pasted into a chat transcript is a leaked key, and the deploy
  script collects them directly instead.

So: don't send passwords or API keys. Nothing below needs them from you in
chat.

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

## 3. Model provider API key

JARVIS speaks to a language model through a swappable adapter, so which
provider answers is a setting, not a rewrite. Two are built in:

| | Cost | Notes |
|---|---|---|
| **Google Gemini** (default) | Free tier: ₹0, capped ~250 requests/day | Your choice for Stage 0 |
| **Anthropic Claude** | Sonnet 5: $2 / $10 per million tokens in/out | Optional; acts as automatic fallback |

### 3a. Get a Gemini key (free)

1. Go to https://aistudio.google.com/apikey and sign in.
2. Click **Create API key**.
3. **Create it in a NEW project, not `jarvis-by-claude-a1026`.**

That last point matters and is easy to get wrong. **Enabling billing on a
Google Cloud project removes its Gemini free tier** — every call then bills
from the first token. Cloud Run *requires* billing, so the project JARVIS
deploys into will have billing on. A key issued from that same project
would quietly stop being free.

Keeping the Gemini key in its own separate, billing-free project is what
keeps Stage 0 at ₹0.

**Don't paste the key here** — the deploy script asks for it directly and
puts it into Google Secret Manager.

### 3b. Optionally add Claude as a fallback

Not required. If you also set an Anthropic key
([console.anthropic.com](https://console.anthropic.com) → API keys), JARVIS
automatically retries with Claude whenever Gemini fails or is rate-limited,
and the reply tells you which one answered. Without it, a Gemini outage
means JARVIS reports the failure plainly rather than answering.

The deploy script offers this as optional — press Enter to skip.

### 3c. A caution about free tiers

Google revised its free-tier quotas down by 50–80% in December 2025
without notice, and doesn't guarantee them. At your usage that's still
comfortably free, but treat ₹0 as "currently free", not "guaranteed free".
`GET /v1/budget` will keep reporting ₹0 while Gemini is priced at zero —
JARVIS logs a warning at startup saying exactly that, so the zero is never
mistaken for verified proof that nothing is being spent. If you ever
enable billing, set the real prices (see `BUDGET.md`).

## 4. Deploy — Google Cloud Run

Deploying needs a command line, which an iPad doesn't have. **Google Cloud
Shell** solves this: it's a full terminal that runs in a browser tab, free,
with the Google Cloud tools already installed.

### 4a. Open Cloud Shell

1. Go to https://console.cloud.google.com/?cloudshell=true and make sure
   the project selector at the top says **jarvis-by-claude-a1026**.
2. Wait for the terminal to appear at the bottom.

Cloud Shell already knows who you are on the Google side — it signs you in
with the same Google account automatically. GitHub is separate, which is
what step 4a-i deals with.

### 4a-i. Give Cloud Shell read access to the repo

This repo is **private**, so cloning it asks for a GitHub username and
password. **Entering your GitHub password will not work** — GitHub stopped
accepting account passwords for git operations in August 2021. What goes
in the password box is an *access token* instead.

Make one that can do as little as possible:

1. Open https://github.com/settings/personal-access-tokens/new
2. **Token name:** `cloud-shell-deploy`
3. **Expiration:** 7 days — you only need it for this deploy
4. **Repository access:** *Only select repositories* → pick
   `J.A.R.V.I.S-an-personal-advanced-operating-system-`
5. **Permissions:** *Repository permissions* → **Contents** → **Read-only**
6. **Generate token**, then copy it

That token can read one repo, can't change anything, and expires by
itself. If it ever leaked, the worst case is someone reading code you were
willing to show me anyway.

### 4a-ii. Clone the code

In Cloud Shell:

```bash
git clone -b claude/new-session-v0jp79 \
  https://github.com/sagarnandani/J.A.R.V.I.S-an-personal-advanced-operating-system-.git jarvis
cd jarvis
```

When it prompts:

- **Username:** `sagarnandani`
- **Password:** paste the token (nothing appears as you paste — that's
  normal, it's hidden on purpose)

Git won't remember the token afterwards, so a later `git pull` asks again.
That's deliberate: nothing writes the token to disk.

**Two alternatives**, if you'd rather not deal with tokens:

- Run `gh auth login` first. If Cloud Shell has GitHub's own tool
  installed, this signs you in through a browser with a short code and no
  token to copy. If you get "command not found", it isn't installed — use
  the token above.
- Make the repo public (GitHub → Settings → General → bottom of the page).
  Then cloning needs no login at all. There are no passwords or keys in
  this repo — everything secret lives in Google Secret Manager — so this is
  safe from a credentials standpoint. It's your call whether you want the
  code visible; nothing about JARVIS requires it either way.

### 4b. Run the deploy

```bash
bash infra/deploy.sh
```

It asks for your Supabase connection string (step 1b) and your Gemini API
key (step 3a), then optionally an Anthropic key for fallback — press Enter
to skip that one. Typing is hidden. Both go straight
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
