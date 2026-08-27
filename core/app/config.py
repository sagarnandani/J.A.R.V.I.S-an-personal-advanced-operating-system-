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
    # The Firebase project's public web config, served to the test console
    # via GET /public/firebase-config. These values are NOT secret -- they
    # identify the project to Firebase's client SDK, the same way a
    # website's own domain isn't secret.
    firebase_api_key: str = ""
    firebase_auth_domain: str = ""
    firebase_project_id: str = ""
    firebase_app_id: str = ""
    # Who is allowed in. JARVIS Stage 0 is explicitly single-user, so
    # exactly one person may authenticate. Identify them either way:
    #
    #   owner_email -- easiest, because you know it before deploying.
    #     Only honoured when the sign-in provider says the address is
    #     verified, so nobody can claim ownership by registering an
    #     unverified account with your address.
    #   owner_uid   -- Firebase's internal user ID. Exact and immutable,
    #     but you can only look it up after signing in at least once.
    #
    # Setting both is fine (either one matching grants access). Setting
    # neither is a misconfiguration and the API refuses all requests.
    owner_uid: str | None = None
    owner_email: str | None = None

    # --- LLM provider ---
    # Which provider answers by default: "gemini", "claude", or "mock".
    # Changing this is a config change, not a code change -- that's what
    # the adapter layer in app/llm/ exists for.
    llm_provider: str = "gemini"
    # When both providers have keys, automatically retry with the other one
    # if the primary fails (architecture doc, section L: graceful
    # degradation). The reply always reports which provider actually
    # answered, so a fallback is never invisible.
    llm_fallback_enabled: bool = True

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"

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
    # USD per 1,000,000 tokens, per provider. Overridable per-deployment so
    # a price change never requires a code change. Verify against the
    # providers' own pricing pages before trusting the budget figure for a
    # real spending decision -- see /docs/BUDGET.md.
    #
    # Claude defaults are Sonnet 5's rates.
    price_claude_input_usd_per_1m: Decimal = Decimal("2.00")
    price_claude_output_usd_per_1m: Decimal = Decimal("10.00")
    # Gemini defaults to zero because the plan is Google's free tier.
    # IMPORTANT: if billing is ever enabled on the Google Cloud project the
    # Gemini key belongs to, the free tier stops applying and real charges
    # begin -- while these zeros would keep reporting Rs.0 spent. That is a
    # silent under-report in the dangerous direction, so the app logs a
    # warning at startup whenever a provider in use is priced at zero.
    price_gemini_input_usd_per_1m: Decimal = Decimal("0.00")
    price_gemini_output_usd_per_1m: Decimal = Decimal("0.00")

    # --- CORS ---
    # Stage 0 has no real client yet, only the test console, so this
    # defaults wide open. Auth (not CORS) is what actually protects the
    # API. Tighten this once a real client domain exists.
    cors_origins: str = "*"


@lru_cache
def get_settings() -> Settings:
    return Settings()
