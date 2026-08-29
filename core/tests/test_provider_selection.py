"""Which provider answers, and what happens when one is down.

These use fake providers rather than real SDK calls -- the behaviour being
tested is the routing and failover logic, which must be verifiable without
network access or API keys.
"""
import pytest

from app.config import Settings
from app.llm import get_provider
from app.llm.base import LLMProvider, LLMResult, Turn
from app.llm.fallback import FallbackProvider
from app.llm.mock_adapter import MockAdapter


class FakeProvider(LLMProvider):
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0
        # Recorded so tests can check that recalled history actually
        # reaches the provider, rather than only that nothing crashed.
        self.last_history: list[Turn] | None = None
        self.last_memory_context: str | None = None

    async def complete(
        self,
        message: str,
        history: list[Turn] | None = None,
        memory_context: str | None = None,
    ) -> LLMResult:
        self.calls += 1
        self.last_history = history
        self.last_memory_context = memory_context
        return LLMResult(
            text=f"{self.name} says hi",
            input_tokens=1,
            output_tokens=1,
            model=self.name,
            provider=self.name,
        )


class BrokenProvider(LLMProvider):
    def __init__(self, error: str = "provider is down") -> None:
        self.error = error
        self.calls = 0

    async def complete(
        self,
        message: str,
        history: list[Turn] | None = None,
        memory_context: str | None = None,
    ) -> LLMResult:
        self.calls += 1
        raise RuntimeError(self.error)


# --- selection -----------------------------------------------------------

def test_no_keys_falls_back_to_the_honest_mock():
    s = Settings(gemini_api_key=None, anthropic_api_key=None)
    assert isinstance(get_provider(s), MockAdapter)


def test_both_keys_gives_a_fallback_pair():
    s = Settings(llm_provider="gemini", gemini_api_key="g", anthropic_api_key="a")
    assert isinstance(get_provider(s), FallbackProvider)


def test_only_one_key_gives_that_provider_alone():
    s = Settings(llm_provider="gemini", gemini_api_key="g", anthropic_api_key=None)
    provider = get_provider(s)
    assert not isinstance(provider, FallbackProvider)
    assert type(provider).__name__ == "GeminiAdapter"


def test_preferred_provider_without_a_key_uses_the_other_one():
    """Asking for Gemini with only a Claude key should still work, loudly,
    rather than failing to start."""
    s = Settings(llm_provider="gemini", gemini_api_key=None, anthropic_api_key="a")
    assert type(get_provider(s)).__name__ == "ClaudeAdapter"


def test_fallback_can_be_switched_off():
    s = Settings(
        llm_provider="gemini",
        gemini_api_key="g",
        anthropic_api_key="a",
        llm_fallback_enabled=False,
    )
    assert not isinstance(get_provider(s), FallbackProvider)


def test_unknown_provider_name_is_rejected_loudly():
    s = Settings(llm_provider="chatgpt", gemini_api_key="g")
    with pytest.raises(ValueError, match="LLM_PROVIDER"):
        get_provider(s)


# --- failover ------------------------------------------------------------

@pytest.mark.asyncio
async def test_primary_answers_and_fallback_is_untouched():
    primary, secondary = FakeProvider("gemini"), FakeProvider("claude")
    result = await FallbackProvider(primary, secondary).complete("hi")
    assert result.provider == "gemini"
    assert secondary.calls == 0


@pytest.mark.asyncio
async def test_fallback_takes_over_when_primary_fails():
    primary, secondary = BrokenProvider(), FakeProvider("claude")
    result = await FallbackProvider(primary, secondary).complete("hi")
    assert result.provider == "claude"
    assert primary.calls == 1


@pytest.mark.asyncio
async def test_the_reply_reports_who_actually_answered():
    """A fallback must never be invisible -- the cost is priced from this
    field, and billing a Gemini answer at Claude's rates would be wrong."""
    result = await FallbackProvider(BrokenProvider(), FakeProvider("claude")).complete("hi")
    assert result.provider == "claude"
    assert result.model == "claude"


@pytest.mark.asyncio
async def test_both_providers_down_reports_both_causes():
    provider = FallbackProvider(
        BrokenProvider("gemini exploded"), BrokenProvider("claude exploded")
    )
    with pytest.raises(RuntimeError) as exc:
        await provider.complete("hi")
    assert "gemini exploded" in str(exc.value)
    assert "claude exploded" in str(exc.value)


# --- what the placeholder reply tells the owner to do ---------------------
#
# When no key works, this text is the entire explanation the owner gets. It
# has to name the setting they actually need to change. It once always said
# ANTHROPIC_API_KEY, which became wrong the day Gemini became the default --
# a wrong instruction is worse than a vague one, because it gets followed.

@pytest.mark.asyncio
async def test_mock_names_the_gemini_key_when_gemini_was_wanted():
    s = Settings(llm_provider="gemini", gemini_api_key=None, anthropic_api_key=None)
    result = await get_provider(s).complete("hello")
    assert "GEMINI_API_KEY" in result.text
    assert "ANTHROPIC_API_KEY" not in result.text


@pytest.mark.asyncio
async def test_mock_names_the_claude_key_when_claude_was_wanted():
    s = Settings(llm_provider="claude", gemini_api_key=None, anthropic_api_key=None)
    result = await get_provider(s).complete("hello")
    assert "ANTHROPIC_API_KEY" in result.text
    assert "GEMINI_API_KEY" not in result.text


@pytest.mark.asyncio
async def test_mock_chosen_deliberately_does_not_nag_about_a_missing_key():
    """LLM_PROVIDER=mock is a choice, not a misconfiguration.

    Naming a key to set would be telling the owner to fix something that
    isn't broken.
    """
    s = Settings(llm_provider="mock")
    result = await get_provider(s).complete("hello")
    assert "API_KEY" not in result.text
