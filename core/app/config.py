"""Central configuration, loaded from environment variables.

Nothing secret is ever hardcoded here. In production these values are
injected as Cloud Run environment variables, with true secrets (API keys,
the Firebase service account) coming from Secret Manager rather than being
set as plain env vars — see /docs/DEPLOYMENT.md.
"""
from decimal import Decimal
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Core wiring ---
    database_url: str = "postgres://jarvis:jarvis@localhost:5432/jarvis"

    # --- Dev mode ---
    # When true: auth is bypassed (every request is treated as the owner)
    # and, if no ANTHROPIC_API_KEY is set, the LLM adapter falls back to a
    # mock responder. This exists so the system can be run and tested on a
    # laptop/CI without live Firebase or Anthropic credentials. It must
    # never be true in the deployed Cloud Run service -- deploy config sets
    # it to false explicitly (see /docs/DEPLOYMENT.md).
    dev_mode: bool = False

    # --- Firebase Auth ---
    # Path to the service account JSON (mounted from Secret Manager in
    # prod). Only required when dev_mode is false.
    firebase_service_account_path: str | None = None
    # The Firebase project's public web config, served to the test console
    # via GET /public/firebase-config. These values are NOT secret -- they
    # identify the project to Firebase's client SDK, the same way a
    # website's own domain isn't secret.
    firebase_api_key: str = ""
    firebase_auth_domain: str = ""
    firebase_project_id: str = ""
    firebase_app_id: str = ""
    # The single owner's Firebase UID. Once set, only this UID may
    # authenticate -- JARVIS Stage 0 is explicitly single-user.
    owner_uid: str | None = None

    # --- LLM provider ---
    anthropic_api_key: str | None = None
    claude_model: str = "claude-sonnet-5"

    # --- Budget guardrail ---
    # A concrete ceiling was requested by the architecture doc (section P)
    # but not fixed by the owner; the Stage 0 brief gives a range
    # (Rs.3,000-4,000/month). We default to the midpoint and make it a
    # one-line env var change -- flagged clearly in /docs/BUDGET.md rather
    # than silently picked.
    monthly_budget_inr: Decimal = Decimal("3500")
    # Anthropic bills in USD per token; we convert to INR for the ceiling
    # the owner actually thinks in. This rate WILL drift from the real
    # exchange rate over time -- it is an estimate, not a live FX lookup,
    # and is called out as such in /docs/BUDGET.md.
    usd_to_inr_rate: Decimal = Decimal("90")
    # USD per 1,000,000 tokens. These are placeholder figures -- verify
    # against https://www.anthropic.com/pricing before trusting the budget
    # dashboard for a real spending decision. Overridable per-deployment so
    # a price change never requires a code change.
    price_input_usd_per_1m: Decimal = Decimal("3.00")
    price_output_usd_per_1m: Decimal = Decimal("15.00")

    # --- CORS ---
    # Stage 0 has no real client yet, only the test console, so this
    # defaults wide open. Auth (not CORS) is what actually protects the
    # API. Tighten this once a real client domain exists.
    cors_origins: str = "*"


@lru_cache
def get_settings() -> Settings:
    return Settings()
