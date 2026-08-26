#!/usr/bin/env bash
#
# Deploy JARVIS Core to Google Cloud Run.
#
# Designed to be run from Google Cloud Shell (a terminal that runs in a
# browser tab, so it works from an iPad) -- see docs/DEPLOYMENT.md step 4.
# Everything specific to this project is filled in already; you only get
# asked for the two secrets, and only the first time.
#
#   bash infra/deploy.sh
#
# Safe to re-run: it skips work that's already done and redeploys with the
# current code. Nothing here deletes anything.

set -euo pipefail

# --- Settings (override by exporting before running, e.g. REGION=...) ---
PROJECT_ID="${PROJECT_ID:-jarvis-by-claude-a1026}"
REGION="${REGION:-asia-south1}"          # Mumbai -- closest to the owner
SERVICE="${SERVICE:-jarvis-core}"
OWNER_EMAIL="${OWNER_EMAIL:-sagarnandani99@gmail.com}"

# Firebase web config -- public identifiers, not secrets.
FIREBASE_API_KEY="${FIREBASE_API_KEY:-AIzaSyCezIYP0fAG-Yq55Z43W4qdwLXEKzC4g1M}"
FIREBASE_AUTH_DOMAIN="${FIREBASE_AUTH_DOMAIN:-jarvis-by-claude-a1026.firebaseapp.com}"
FIREBASE_PROJECT_ID="${FIREBASE_PROJECT_ID:-jarvis-by-claude-a1026}"
FIREBASE_APP_ID="${FIREBASE_APP_ID:-1:701562415519:web:1d377f036cd62c4c66b5aa}"

# Budget guardrail -- see docs/BUDGET.md.
MONTHLY_BUDGET_INR="${MONTHLY_BUDGET_INR:-3500}"
USD_TO_INR_RATE="${USD_TO_INR_RATE:-90}"
CLAUDE_MODEL="${CLAUDE_MODEL:-claude-sonnet-5}"

IMAGE="gcr.io/${PROJECT_ID}/${SERVICE}"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

say "Using project ${PROJECT_ID}, region ${REGION}"
gcloud config set project "${PROJECT_ID}" --quiet

say "Enabling the Google Cloud services this needs (no-op if already on)"
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  containerregistry.googleapis.com \
  --quiet

# --- Secrets -------------------------------------------------------------
# Both of these are real credentials, so they go into Secret Manager rather
# than being passed as plain environment variables. Environment variables
# are visible to anyone who can view the service in the Cloud Console, and
# they show up in deployment logs; Secret Manager values do not. The
# database URL counts as a secret because it contains the database
# password.
ensure_secret() {
  local name="$1" prompt="$2"
  if gcloud secrets describe "${name}" --quiet >/dev/null 2>&1; then
    echo "  secret '${name}' already exists -- leaving it alone."
    return
  fi
  echo
  echo "  ${prompt}"
  echo "  (typing is hidden; paste and press Enter)"
  local value
  read -r -s value
  if [[ -z "${value}" ]]; then
    echo "  Nothing entered -- aborting so nothing is half-configured." >&2
    exit 1
  fi
  printf '%s' "${value}" \
    | gcloud secrets create "${name}" --data-file=- --replication-policy=automatic --quiet
  echo "  stored '${name}'."
}

say "Checking secrets"
ensure_secret jarvis-database-url \
  "Paste your Supabase SESSION POOLER connection string (docs/DEPLOYMENT.md step 1b):"
ensure_secret jarvis-anthropic-api-key \
  "Paste your Anthropic API key (starts with sk-ant-):"

say "Granting the service permission to read those secrets"
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
for secret in jarvis-database-url jarvis-anthropic-api-key; do
  gcloud secrets add-iam-policy-binding "${secret}" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role=roles/secretmanager.secretAccessor \
    --quiet >/dev/null
done
echo "  done (${RUNTIME_SA})"

say "Building the container image (this takes a few minutes the first time)"
gcloud builds submit --config infra/cloudbuild.yaml --substitutions=_IMAGE="${IMAGE}" --quiet

say "Deploying to Cloud Run"
gcloud run deploy "${SERVICE}" \
  --image="${IMAGE}" \
  --region="${REGION}" \
  --platform=managed \
  --allow-unauthenticated \
  --min-instances=0 \
  --max-instances=2 \
  --set-env-vars="DEV_MODE=false,OWNER_EMAIL=${OWNER_EMAIL},CLAUDE_MODEL=${CLAUDE_MODEL},MONTHLY_BUDGET_INR=${MONTHLY_BUDGET_INR},USD_TO_INR_RATE=${USD_TO_INR_RATE},FIREBASE_API_KEY=${FIREBASE_API_KEY},FIREBASE_AUTH_DOMAIN=${FIREBASE_AUTH_DOMAIN},FIREBASE_PROJECT_ID=${FIREBASE_PROJECT_ID},FIREBASE_APP_ID=${FIREBASE_APP_ID}" \
  --set-secrets="DATABASE_URL=jarvis-database-url:latest,ANTHROPIC_API_KEY=jarvis-anthropic-api-key:latest" \
  --quiet

URL="$(gcloud run services describe "${SERVICE}" --region="${REGION}" --format='value(status.url)')"

say "Deployed"
echo "  JARVIS is at: ${URL}"
echo
echo "  Check it's alive:   ${URL}/health"
echo "  Open the console:   ${URL}"
echo
echo "  Add this URL to Firebase's authorised domains or Google sign-in"
echo "  will be refused -- see docs/DEPLOYMENT.md step 4c:"
echo "  https://console.firebase.google.com/project/${FIREBASE_PROJECT_ID}/authentication/settings"
