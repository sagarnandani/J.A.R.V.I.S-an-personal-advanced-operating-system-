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

### 2c. Register a web app and copy four values

1. Click the **gear icon → Project settings**.
2. Scroll to **Your apps**, click the **web icon** (`</>`).
3. Give it a nickname (`jarvis-web`), **don't** tick Firebase Hosting,
   click **Register app**.
4. Firebase shows you a code block. You need exactly four values from it:
   `apiKey`, `authDomain`, `projectId`, `appId`.

**Send me those four values** — they're safe to share. They're public
identifiers that ship inside any web page using Firebase, not credentials;
they identify your project the way a street address identifies a house.
The thing that actually protects JARVIS is the owner check in 2e.

### 2d. Download the service account key — this one IS secret

1. **Project settings → Service accounts** tab.
2. Click **Generate new private key** → **Generate key**. A `.json` file
   downloads.

**Do NOT send me this file, and don't put it in the repo.** It lets
anything holding it act as your Firebase project's administrator. It goes
into Google Secret Manager in step 3, and nowhere else.

(If you'd rather skip handling this file entirely: when JARVIS runs on
Google Cloud Run it can authenticate automatically using the service
account Cloud Run already gives it, provided the Firebase and Cloud Run
projects are the same one. If you create the Firebase project *inside*
your existing Google Cloud project, you can skip this download. Tell me
which way you went and I'll set the config accordingly.)

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

## 3. Secrets — Google Secret Manager

Store these as secrets, not plain env vars, in whatever platform you
deploy to:
- `ANTHROPIC_API_KEY`
- the Firebase service account JSON (step 2.4)

Everything else in `.env.example` is non-secret config and can be a plain
Cloud Run environment variable.

## 4. Compute — Google Cloud Run

1. Create/select a GCP project, enable the Cloud Run and Secret Manager APIs.
2. Build and push the image (from the repo root):
   ```bash
   gcloud builds submit --tag gcr.io/<your-project-id>/jarvis-core -f infra/Dockerfile .
   ```
3. Deploy:
   ```bash
   gcloud run deploy jarvis-core \
     --image gcr.io/<your-project-id>/jarvis-core \
     --platform managed \
     --region <a region near you> \
     --allow-unauthenticated \
     --set-env-vars DATABASE_URL=...,DEV_MODE=false,OWNER_EMAIL=...,CLAUDE_MODEL=claude-sonnet-5,MONTHLY_BUDGET_INR=3500,USD_TO_INR_RATE=90,PRICE_INPUT_USD_PER_1M=3.00,PRICE_OUTPUT_USD_PER_1M=15.00,FIREBASE_API_KEY=...,FIREBASE_AUTH_DOMAIN=...,FIREBASE_PROJECT_ID=...,FIREBASE_APP_ID=... \
     --set-secrets ANTHROPIC_API_KEY=anthropic-api-key:latest,FIREBASE_SERVICE_ACCOUNT_PATH=/secrets/firebase-sa.json=firebase-service-account:latest
   ```
   (`--allow-unauthenticated` at the Cloud Run layer is intentional and
   safe here — the API itself still requires a valid Firebase ID token
   matching `OWNER_UID` for every real endpoint; only `/health` and the
   static test console are meant to be reachable without one.)
4. **Double-check `DEV_MODE` is `false`** in the deployed environment.
   `DEV_MODE=true` disables login entirely and must only ever be used
   locally.
5. Verify: `curl https://<the deployed URL>/health` →
   `{"status": "ok", ...}`.

**Why Cloud Run over a VM:** scales to zero when idle, so a solo user
generates near-$0 compute cost between uses — exactly the brief's
requirement, and the container is identical if you later redeploy it to a
Mac mini or home server (that's the whole point of containerizing it).

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
