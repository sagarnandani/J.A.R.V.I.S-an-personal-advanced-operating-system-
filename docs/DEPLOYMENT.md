# Deployment checklist

I (Claude Code) don't have your cloud accounts' credentials, so I can't
click through these steps for you in this session — but I can do them
*with* you if you paste in the relevant keys/tokens when we get there, or
you can follow this yourself. Each step says which is which.

Nothing here costs money at Stage 0 usage levels if you stick to the
tiers named below — see `BUDGET.md`.

## 1. Database — Supabase (free tier)

1. Create a free account at supabase.com, create a new project.
2. From the project's Settings → Database, copy the **connection string**
   (the "URI" one, not the pooler one, for Stage 0's low traffic). This is
   your `DATABASE_URL`.
3. Apply the schema:
   ```bash
   DATABASE_URL="<the connection string>" python3 db/migrate.py
   ```
   You should see `Applying 001_init.sql ... done.`

**Why Supabase over Cloud SQL:** the brief's own table listed Supabase
free tier as the preferred option "if it meets needs — genuinely $0 at
this scale," ahead of Cloud SQL's smallest paid tier. Stage 0's usage
(one user, occasional messages) is well inside Supabase's free tier, so
that's what these instructions assume. Cloud SQL works identically if you
ever want to switch — it's the same `DATABASE_URL` env var either way.

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
