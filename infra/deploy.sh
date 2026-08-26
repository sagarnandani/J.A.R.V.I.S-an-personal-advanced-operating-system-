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

# Which model provider answers by default. Gemini's free tier keeps Stage 0
# at zero cost; Claude is used automatically as a fallback if its key is
# also set. Change this line (or export LLM_PROVIDER) to swap them.
LLM_PROVIDER="${LLM_PROVIDER:-gemini}"
GEMINI_MODEL="${GEMINI_MODEL:-gemini-2.5-flash}"
CLAUDE_MODEL="${CLAUDE_MODEL:-claude-sonnet-5}"

AR_REPO="${AR_REPO:-jarvis}"
# Artifact Registry, not gcr.io: Container Registry was shut down in March
# 2025 and gcr.io URLs only still resolve for projects that were migrated.
# A brand-new project has no such mapping, so pushing to gcr.io fails.
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/${SERVICE}"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

say "Using project ${PROJECT_ID}, region ${REGION}"
gcloud config set project "${PROJECT_ID}" --quiet

say "Enabling the Google Cloud services this needs (no-op if already on)"
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  compute.googleapis.com \
  --quiet

say "Making sure the container repository exists"
if gcloud artifacts repositories describe "${AR_REPO}" \
     --location="${REGION}" --quiet >/dev/null 2>&1; then
  echo "  repository '${AR_REPO}' already exists."
else
  gcloud artifacts repositories create "${AR_REPO}" \
    --repository-format=docker \
    --location="${REGION}" \
    --description="JARVIS container images" \
    --quiet
  echo "  created repository '${AR_REPO}' in ${REGION}."
fi

# --- Secrets -------------------------------------------------------------
# Both of these are real credentials, so they go into Secret Manager rather
# than being passed as plain environment variables. Environment variables
# are visible to anyone who can view the service in the Cloud Console, and
# they show up in deployment logs; Secret Manager values do not. The
# database URL counts as a secret because it contains the database
# password.
# optional=yes means pressing Enter skips it rather than aborting.
ensure_secret() {
  local name="$1" prompt="$2" optional="${3:-no}"
  if gcloud secrets describe "${name}" --quiet >/dev/null 2>&1; then
    echo "  secret '${name}' already exists -- leaving it alone."
    SECRET_PRESENT+=("${name}")
    return
  fi
  echo
  echo "  ${prompt}"
  if [[ "${optional}" == "yes" ]]; then
    echo "  (optional -- press Enter to skip)"
  else
    echo "  (typing is hidden; paste and press Enter)"
  fi
  local value
  read -r -s value
  if [[ -z "${value}" ]]; then
    if [[ "${optional}" == "yes" ]]; then
      echo "  skipped."
      return
    fi
    echo "  Nothing entered -- aborting so nothing is half-configured." >&2
    exit 1
  fi
  printf '%s' "${value}" \
    | gcloud secrets create "${name}" --data-file=- --replication-policy=automatic --quiet
  echo "  stored '${name}'."
  SECRET_PRESENT+=("${name}")
}

say "Checking secrets"
SECRET_PRESENT=()
ensure_secret jarvis-database-url \
  "Paste your Supabase SESSION POOLER connection string (docs/DEPLOYMENT.md step 1b):"
ensure_secret jarvis-gemini-api-key \
  "Paste your Google Gemini API key (docs/DEPLOYMENT.md step 3a):"
ensure_secret jarvis-anthropic-api-key \
  "Paste an Anthropic API key to use Claude as automatic fallback:" yes

if [[ ! " ${SECRET_PRESENT[*]} " =~ " jarvis-gemini-api-key " \
   && ! " ${SECRET_PRESENT[*]} " =~ " jarvis-anthropic-api-key " ]]; then
  echo
  echo "  WARNING: no model provider key is configured. JARVIS will deploy and" >&2
  echo "  run, but every reply will be a clearly-labelled placeholder saying no" >&2
  echo "  real model was called. Re-run this script once you have a key." >&2
fi

say "Granting the service permission to read those secrets"
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
# The account Cloud Run runs as by default. It is created when the Compute
# Engine API is enabled (done above); on a very new project that can lag by
# a few seconds, so check rather than assuming.
RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
if ! gcloud iam service-accounts describe "${RUNTIME_SA}" --quiet >/dev/null 2>&1; then
  echo "  Waiting for the default service account to appear..."
  for _ in 1 2 3 4 5 6; do
    sleep 10
    gcloud iam service-accounts describe "${RUNTIME_SA}" --quiet >/dev/null 2>&1 && break
  done
fi
if ! gcloud iam service-accounts describe "${RUNTIME_SA}" --quiet >/dev/null 2>&1; then
  echo "  Could not find ${RUNTIME_SA}." >&2
  echo "  This normally appears once the Compute Engine API finishes enabling." >&2
  echo "  Wait a minute and re-run this script -- it will pick up where it left off." >&2
  exit 1
fi
for secret in "${SECRET_PRESENT[@]}"; do
  gcloud secrets add-iam-policy-binding "${secret}" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role=roles/secretmanager.secretAccessor \
    --quiet >/dev/null
done
echo "  done (${RUNTIME_SA})"

say "Building the container image (this takes a few minutes the first time)"
gcloud builds submit --config infra/cloudbuild.yaml --substitutions=_IMAGE="${IMAGE}" --quiet

# Only reference secrets that exist -- naming a missing one fails the deploy.
SECRET_FLAGS="DATABASE_URL=jarvis-database-url:latest"
for s_name in "${SECRET_PRESENT[@]}"; do
  case "${s_name}" in
    jarvis-gemini-api-key)    SECRET_FLAGS+=",GEMINI_API_KEY=jarvis-gemini-api-key:latest" ;;
    jarvis-anthropic-api-key) SECRET_FLAGS+=",ANTHROPIC_API_KEY=jarvis-anthropic-api-key:latest" ;;
  esac
done

say "Deploying to Cloud Run"
gcloud run deploy "${SERVICE}" \
  --image="${IMAGE}" \
  --region="${REGION}" \
  --platform=managed \
  --allow-unauthenticated \
  --min-instances=0 \
  --max-instances=2 \
  --set-env-vars="DEV_MODE=false,OWNER_EMAIL=${OWNER_EMAIL},LLM_PROVIDER=${LLM_PROVIDER},GEMINI_MODEL=${GEMINI_MODEL},CLAUDE_MODEL=${CLAUDE_MODEL},MONTHLY_BUDGET_INR=${MONTHLY_BUDGET_INR},USD_TO_INR_RATE=${USD_TO_INR_RATE},FIREBASE_API_KEY=${FIREBASE_API_KEY},FIREBASE_AUTH_DOMAIN=${FIREBASE_AUTH_DOMAIN},FIREBASE_PROJECT_ID=${FIREBASE_PROJECT_ID},FIREBASE_APP_ID=${FIREBASE_APP_ID}" \
  --set-secrets="${SECRET_FLAGS}" \
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
