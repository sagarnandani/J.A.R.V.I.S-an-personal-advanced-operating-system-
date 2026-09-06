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
    # OAuth client ID for Google's in-page sign-in button. Public, not a
    # secret -- it identifies the app to Google, and Google separately
    # refuses to work from any web address not registered against it.
    google_client_id: str = ""
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

    # --- Staying signed in ---
    # The key used to sign login cookies. Anyone who knows it can mint a
    # cookie claiming to be the owner, so it is a real secret: set it in
    # Render's environment, never in the repo.
    #
    # If it is left empty the server generates a random one at startup.
    # That is safe, but it is different every time the service restarts,
    # so every restart signs you out again. A restart happens on every
    # redeploy -- which is exactly the "it keeps forgetting me" symptom
    # this whole mechanism exists to fix. Set it.
    session_secret: str = ""
    # How long a sign-in lasts before you have to tap the button again.
    session_days: int = 30
    # Whether the login cookie is marked "Secure" -- meaning the browser
    # will only ever send it over HTTPS.
    #
    # True is right for anything with a real address, and is the default
    # precisely because getting this wrong the safe way costs a local
    # inconvenience, while getting it wrong the unsafe way sends your
    # session over the open network in clear text.
    #
    # Set it to false ONLY for a plain-http run on your own machine
    # (http://localhost). Otherwise the browser silently declines to send
    # the cookie back, and sign-in appears to succeed and then instantly
    # fail -- with nothing on screen explaining why.
    cookie_secure: bool = True

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
    # How much internal "thinking" Gemini may do before answering.
    #
    # Gemini's flash models reason to themselves first. That reasoning is
    # never shown, is billed as output, and is time the owner spends
    # watching a spinner. For a conversational assistant it buys very
    # little: these are chat replies, not maths proofs.
    #
    #   -1 -- send no setting at all; the model decides. The default.
    #   0  -- off. Faster and cheaper, on models that allow it.
    #   >0 -- a token budget, for when answers genuinely need working out.
    #
    # The default is -1 -- meaning "don't ask" -- because gemini-3.6-flash
    # refused 0 outright, with a 400 that broke every message until it was
    # reverted. Defaulting to something a known model rejects would waste a
    # failed call on every restart, forever.
    #
    # It is still worth trying on another model: 0 makes replies both
    # faster and cheaper where it is accepted, and the adapter now falls
    # back cleanly if it is refused instead of failing the message.
    gemini_thinking_budget: int = -1
    # Google retires model names. When it does, the API answers 404 and
    # names the replacement in the error, which JARVIS shows you verbatim
    # -- so the fix is always a one-line env var change, never a code
    # change. gemini-2.5-flash was retired for new keys; this is what
    # Google's own error pointed to.
    gemini_model: str = "gemini-3.6-flash"

    anthropic_api_key: str | None = None
    claude_model: str = "claude-sonnet-5"

    # --- Memory recall ---
    # Whether past conversation is fed back to the model. Off means JARVIS
    # answers each message in isolation, the way Stage 0 did. Kept as a
    # switch because recall is the one feature here that costs money on
    # every single message, so being able to turn it off without a deploy
    # is worth one boolean.
    memory_recall_enabled: bool = True
    # How many past turns to consider. A "turn" is one thing said by one
    # side, so 20 is roughly 10 exchanges.
    memory_recall_turns: int = 20
    # And how much text those turns may total. This is the limit that
    # actually protects the budget: the whole history is re-sent with
    # every message, so without a ceiling each message in a long
    # conversation costs more than the last, forever. Roughly 8000
    # characters is about 2000 tokens -- see /docs/BUDGET.md.
    memory_recall_max_chars: int = 8000

    # --- Long-term memory (facts) ---
    # Conversation recall only reaches back about ten exchanges. This is
    # what keeps the things worth keeping -- birthdays, preferences,
    # decisions -- and looks them up by relevance however long ago they
    # were said.
    #
    # It costs a second model call per message, made AFTER the reply is
    # sent so it never adds to the wait. On a free tier the cost is quota,
    # not money: roughly half as many messages per day. Hence the switch.
    memory_facts_enabled: bool = True
    # How many facts may go into a message, and how much text they may
    # total. The same budget logic as conversation recall, for the same
    # reason: this is re-sent with every message.
    memory_facts_limit: int = 25
    memory_facts_max_chars: int = 2000
    # Most exchanges yield nothing worth keeping, and one that yields ten
    # "facts" is usually a model padding rather than a genuinely dense
    # message.
    memory_facts_per_exchange: int = 5

    # --- Model tiers (see app/agents/model_router.py) ---
    # Agents ask for cheap/standard/deep, never a model name. These map
    # the tiers onto real models, so a provider retiring a name is an
    # environment change rather than a code change.
    model_cheap: str = ""
    model_deep: str = ""

    # --- Live voice ---
    # Gemini speaking directly, rather than the browser reading text
    # aloud. Model and voice are settings because a model name that
    # Google retires would otherwise need a code change -- which has
    # already happened once on this project.
    live_model: str = "gemini-3.1-flash-live-preview"
    # Kore, Fenrir, Charon, Puck, Zephyr. Kore is the composed male voice.
    live_voice: str = "Kore"

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
