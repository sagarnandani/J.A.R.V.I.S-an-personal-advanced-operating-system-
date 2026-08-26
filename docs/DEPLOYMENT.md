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

1. Create a free Firebase project at console.firebase.google.com.
2. Authentication → Sign-in method → enable **Google** (simplest for a
   single owner; email/password also works if you prefer).
3. Project settings → General → "Your apps" → add a **Web app**. Copy the
   config object's `apiKey`, `authDomain`, `projectId`, `appId` — these
   are public values, not secrets. Set them as `FIREBASE_API_KEY`,
   `FIREBASE_AUTH_DOMAIN`, `FIREBASE_PROJECT_ID`, `FIREBASE_APP_ID`.
4. Project settings → Service accounts → **Generate new private key**.
   This downloads a JSON file — this one *is* a secret (it lets code act
   as your Firebase project's admin). Never commit it. In Cloud Run,
   store it in Secret Manager (step 4 below) and mount it; locally, save
   it somewhere outside the repo and point
   `FIREBASE_SERVICE_ACCOUNT_PATH` at it.
5. **Set the owner**: sign in once through the test console (after step 3
   deploy) or via `curl` to get your Firebase UID — it's in the decoded ID
   token, or visible in Firebase Console → Authentication → Users after
   your first sign-in. Set `OWNER_UID` to that value and redeploy. Until
   this is set, the API returns a 500 telling you so (see `app/auth.py`)
   rather than silently accepting anyone.

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
     --set-env-vars DATABASE_URL=...,DEV_MODE=false,OWNER_UID=...,CLAUDE_MODEL=claude-sonnet-5,MONTHLY_BUDGET_INR=3500,USD_TO_INR_RATE=90,PRICE_INPUT_USD_PER_1M=3.00,PRICE_OUTPUT_USD_PER_1M=15.00,FIREBASE_API_KEY=...,FIREBASE_AUTH_DOMAIN=...,FIREBASE_PROJECT_ID=...,FIREBASE_APP_ID=... \
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
