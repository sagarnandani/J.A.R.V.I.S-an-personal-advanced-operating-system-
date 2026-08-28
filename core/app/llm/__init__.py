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
MOCK = "mock"


def _build(name: str, settings: Settings) -> LLMProvider | None:
    """Build one provider, or None if it isn't configured on this deployment."""
    if name == GEMINI and settings.gemini_api_key:
        from app.llm.gemini_adapter import GeminiAdapter

        return GeminiAdapter(
            api_key=settings.gemini_api_key, model=settings.gemini_model
        )

    if name == CLAUDE and settings.anthropic_api_key:
        from app.llm.claude_adapter import ClaudeAdapter

        return ClaudeAdapter(
            api_key=settings.anthropic_api_key, model=settings.claude_model
        )

    return None


def _other(name: str) -> str:
    return CLAUDE if name == GEMINI else GEMINI


def get_provider(settings: Settings) -> LLMProvider:
    """Pick the provider (and its fallback) for this deployment.

    Order of preference:
      1. The configured primary, with the other provider as automatic
         fallback if that one is also configured.
      2. Whichever provider IS configured, if the preferred one has no key.
      3. The mock adapter, which answers honestly that no real model was
         called -- so the system still runs end-to-end with no keys at all.
    """
    preferred = settings.llm_provider.strip().lower()
    if preferred not in (GEMINI, CLAUDE, MOCK):
        raise ValueError(
            f"LLM_PROVIDER must be one of 'gemini', 'claude', or 'mock' -- got {preferred!r}"
        )

    if preferred == MOCK:
        from app.llm.mock_adapter import MockAdapter

        return MockAdapter()

    primary = _build(preferred, settings)
    secondary = _build(_other(preferred), settings)

    if primary is None and secondary is None:
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
            "LLM_PROVIDER is '%s' but no API key is set for it -- using '%s' instead.",
            preferred,
            _other(preferred),
        )
        return secondary

    if secondary is None or not settings.llm_fallback_enabled:
        return primary

    from app.llm.fallback import FallbackProvider

    return FallbackProvider(primary, secondary)
