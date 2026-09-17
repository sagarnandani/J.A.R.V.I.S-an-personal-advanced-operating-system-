"""LLM provider adapters.

See base.py for the interface every provider implements. Which provider
answers is a configuration choice (`LLM_PROVIDER`), not a code change --
that's the whole point of this package existing.
"""
import logging

from app.config import Settings
from app.llm.base import LLMProvider

logger = logging.getLogger("jarvis.llm")

GEMINI = "gemini"
CLAUDE = "claude"
OPENAI = "openai"
MOCK = "mock"

# Every real provider, in the order they are tried as fallbacks for one
# another. A list rather than a pair: the code used to ask for "the other
# one", which quietly assumed there would only ever be two of them.
REAL = (GEMINI, OPENAI, CLAUDE)

# Named so that being asked for one produces an honest answer rather than
# a silent substitution. A provider with no adapter is not "unavailable";
# it was never built, and those are different things to be told.
NOT_BUILT = {
    "local": "No local model adapter is built yet.",
}


class ProviderUnavailable(Exception):
    """The owner asked for a specific provider and it cannot answer.

    Raised rather than swallowed. A hard override that quietly became a
    different model would turn a deliberate choice into a suggestion, and
    he would have no way of knowing it had happened.
    """

    def __init__(self, provider: str, why: str) -> None:
        super().__init__(why)
        self.provider = provider
        self.why = why


def _build(name: str, settings: Settings) -> LLMProvider | None:
    """Build one provider, or None if it isn't configured on this deployment."""
    if name == GEMINI and settings.gemini_api_key:
        from app.llm.gemini_adapter import GeminiAdapter

        return GeminiAdapter(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            thinking_budget=settings.gemini_thinking_budget,
        )

    if name == CLAUDE and settings.anthropic_api_key:
        from app.llm.claude_adapter import ClaudeAdapter

        return ClaudeAdapter(
            api_key=settings.anthropic_api_key, model=settings.claude_model
        )

    if name == OPENAI and settings.openai_api_key:
        from app.llm.openai_adapter import OpenAIAdapter

        return OpenAIAdapter(
            api_key=settings.openai_api_key, model=settings.openai_model
        )

    return None


def _others(name: str) -> tuple[str, ...]:
    """Every other real provider, in the order they should be tried.

    This replaced `_other`, which returned a single provider and worked
    only while there were exactly two. Adding a third to that shape would
    have silently made one of them unreachable as a fallback.
    """
    return tuple(p for p in REAL if p != name)


def _chain(providers: list[LLMProvider]) -> LLMProvider:
    """One provider that tries each in turn.

    FallbackProvider takes a pair, and is itself a provider, so three are
    nested rather than needing a different class.
    """
    from app.llm.fallback import FallbackProvider

    chained = providers[-1]
    for earlier in reversed(providers[:-1]):
        chained = FallbackProvider(earlier, chained)
    return chained


def get_provider(settings: Settings, want=None) -> LLMProvider:
    """Pick the provider (and its fallback) for this deployment.

    `want` is the owner's own choice for this piece of work, if he made
    one. It decides WHO does the work and nothing else: permissions,
    budgets and approvals come from the agent's registry entry and the
    runtime, and a preference cannot widen any of them.

    A hard choice that cannot be honoured raises `ProviderUnavailable`
    rather than falling back, because falling back silently is the
    failure this exists to prevent. A soft one falls back and the result
    says which provider actually answered.

    With no choice made:
      1. The configured primary, with the other provider as automatic
         fallback if that one is also configured.
      2. Whichever provider IS configured, if the preferred one has no key.
      3. The mock adapter, which answers honestly that no real model was
         called -- so the system still runs end-to-end with no keys at all.
    """
    if want is not None and getattr(want, "provider", None):
        chosen = _for_preference(settings, want)
        if chosen is not None:
            return chosen

    preferred = settings.llm_provider.strip().lower()
    if preferred not in (*REAL, MOCK):
        raise ValueError(
            f"LLM_PROVIDER must be one of "
            f"{', '.join(repr(p) for p in (*REAL, MOCK))} -- got {preferred!r}"
        )

    if preferred == MOCK:
        from app.llm.mock_adapter import MockAdapter

        return MockAdapter()

    primary = _build(preferred, settings)
    others = [built for built in
              (_build(name, settings) for name in _others(preferred))
              if built is not None]

    if primary is None and not others:
        from app.llm.mock_adapter import MockAdapter

        logger.warning(
            "No model provider API key is configured, so JARVIS will return "
            "clearly-labelled placeholder replies instead of calling a real "
            "model. Set GEMINI_API_KEY or ANTHROPIC_API_KEY to fix this."
        )
        # Told which provider was wanted, so the placeholder reply names the
        # key that is actually missing rather than a generic one. This is
        # the message the owner reads when their key isn't working, so it
        # should point at the setting they need to change.
        return MockAdapter(intended_provider=preferred)

    if primary is None:
        logger.warning(
            "LLM_PROVIDER is '%s' but no API key is set for it -- using the "
            "configured provider(s) instead.", preferred,
        )
        return _chain(others)

    if not others or not settings.llm_fallback_enabled:
        return primary

    return _chain([primary, *others])


def _for_preference(settings: Settings, want) -> LLMProvider | None:
    """Honour what the owner asked for, or say why it cannot be.

    Returns None only when the preference is soft and unmet, which is the
    one case where carrying on with the ordinary choice is right -- and
    the caller reports the fallback.
    """
    from app.llm.preference import HARD

    provider = want.provider
    hard = want.mode == HARD

    if provider in NOT_BUILT:
        if hard:
            raise ProviderUnavailable(provider, NOT_BUILT[provider])
        logger.info("Preferred provider %r is not built; using the usual one.",
                    provider)
        return None

    if provider == MOCK:
        from app.llm.mock_adapter import MockAdapter

        return MockAdapter()

    built = _build(provider, settings)
    if built is not None:
        # Alone, with no fallback wrapper. A named provider that quietly
        # became the other one is exactly what a hard choice rules out,
        # and a soft one that falls back should say so -- which the caller
        # can only do if it knows the fallback happened.
        return built

    why = (
        f"{provider.title()} is not configured on this deployment: its API "
        f"key is not set."
    )
    if hard:
        raise ProviderUnavailable(provider, why)
    logger.info("Preferred provider %r is unavailable; using the usual one.",
                provider)
    return None
