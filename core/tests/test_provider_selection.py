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


# --- three providers, where the code assumed two ---------------------------
#
# `_other(name)` returned a single provider and was correct only while
# there were exactly two. Adding OpenAI to that shape would have made one
# of the three unreachable as a fallback, silently -- everything would
# still work, one provider would simply never be tried.


def test_every_real_provider_can_be_the_configured_one():
    from app.llm import REAL, get_provider

    for name in REAL:
        settings = Settings(llm_provider=name, gemini_api_key="g",
                            anthropic_api_key="a", openai_api_key="o")
        chosen = get_provider(settings)
        assert chosen is not None, f"{name} could not be selected"


def test_each_provider_falls_back_to_both_of_the_others():
    from app.llm import REAL, _others

    for name in REAL:
        others = _others(name)
        assert name not in others
        assert set(others) == set(REAL) - {name}, (
            f"{name} cannot fall back to every other provider"
        )


def test_openai_alone_is_used_without_a_fallback_wrapper():
    from app.llm import get_provider

    chosen = get_provider(Settings(llm_provider="openai", openai_api_key="o"))
    assert chosen.__class__.__name__ == "OpenAIAdapter"


def test_all_three_configured_chains_all_three():
    """A chain, not a pair. If this returned a two-deep structure, the
    third provider would never be reached."""
    from app.llm import get_provider

    chosen = get_provider(Settings(llm_provider="gemini", gemini_api_key="g",
                                   anthropic_api_key="a", openai_api_key="o"))
    names, queue = [], [chosen]
    while queue:
        node = queue.pop()
        if node.__class__.__name__ == "FallbackProvider":
            queue += [node._primary, node._secondary]
        else:
            names.append(node.__class__.__name__)
    assert sorted(names) == ["ClaudeAdapter", "GeminiAdapter", "OpenAIAdapter"]


def test_the_configured_provider_is_tried_first():
    """Order is the whole point of a fallback chain."""
    from app.llm import get_provider

    chosen = get_provider(Settings(llm_provider="openai", openai_api_key="o",
                                   gemini_api_key="g", anthropic_api_key="a"))
    assert chosen.__class__.__name__ == "FallbackProvider"
    assert chosen._primary.__class__.__name__ == "OpenAIAdapter"


def test_a_configured_provider_with_no_key_uses_the_ones_that_have_keys():
    from app.llm import get_provider

    chosen = get_provider(Settings(llm_provider="openai", gemini_api_key="g"))
    assert chosen.__class__.__name__ == "GeminiAdapter"


def test_an_unknown_provider_name_names_all_three():
    from app.llm import get_provider

    with pytest.raises(ValueError) as bad:
        get_provider(Settings(llm_provider="banana"))
    for name in ("gemini", "openai", "claude", "mock"):
        assert name in str(bad.value)


# --- the adapter itself ----------------------------------------------------

@pytest.mark.asyncio
async def test_openai_sends_the_system_prompt_as_a_message(monkeypatch):
    """OpenAI has no separate system field, unlike Claude. Getting this
    wrong loses the persona silently: every reply still arrives, and none
    of them know who they are."""
    from types import SimpleNamespace

    from app.llm.openai_adapter import OpenAIAdapter

    sent = {}

    class FakeCompletions:
        async def create(self, **kwargs):
            sent.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content="hello"))],
                usage=SimpleNamespace(prompt_tokens=11, completion_tokens=22),
            )

    adapter = OpenAIAdapter(api_key="x", model="gpt-test")
    adapter._client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions()))

    result = await adapter.complete("hi", memory_context="he likes tea")

    assert sent["messages"][0]["role"] == "system"
    assert "he likes tea" in sent["messages"][0]["content"]
    assert sent["messages"][-1] == {"role": "user", "content": "hi"}
    assert result.text == "hello"
    assert result.provider == "openai"
    assert (result.input_tokens, result.output_tokens) == (11, 22)


@pytest.mark.asyncio
async def test_a_retired_openai_model_says_which_setting_to_change():
    """The raw 404 names the model and not the remedy. Read on a phone,
    that is the difference between a one-line fix and a message to me."""
    from types import SimpleNamespace

    from app.llm.openai_adapter import OpenAIAdapter

    class Broken:
        async def create(self, **kwargs):
            raise RuntimeError(
                "Error code: 404 - The model `gpt-old` does not exist")

    adapter = OpenAIAdapter(api_key="x", model="gpt-old")
    adapter._client = SimpleNamespace(
        chat=SimpleNamespace(completions=Broken()))

    with pytest.raises(RuntimeError) as failed:
        await adapter.complete("hi")
    assert "OPENAI_MODEL" in str(failed.value)
    assert "gpt-old" in str(failed.value)


@pytest.mark.asyncio
async def test_missing_token_counts_are_zero_rather_than_invented():
    """A cost of zero must mean 'not measured', never a number made up
    here -- the budget ceiling is computed from these."""
    from types import SimpleNamespace

    from app.llm.openai_adapter import OpenAIAdapter

    class NoUsage:
        async def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="x"))],
                usage=SimpleNamespace(),
            )

    adapter = OpenAIAdapter(api_key="x", model="gpt-test")
    adapter._client = SimpleNamespace(
        chat=SimpleNamespace(completions=NoUsage()))

    result = await adapter.complete("hi")
    assert (result.input_tokens, result.output_tokens) == (0, 0)


# --- reporting which providers are configured ------------------------------
#
# "I added the key" and "the key reached the container" are different
# statements. Checking used to mean trusting the first or printing an
# environment dump, which is how a key ends up pasted into a chat window.


def test_health_says_which_providers_are_configured_and_never_which_keys():
    from app.routes.health import _providers

    import app.routes.health as health

    health.get_settings.cache_clear()
    import os

    os.environ["GEMINI_API_KEY"] = "sk-secret-value-do-not-print"
    os.environ.pop("OPENAI_API_KEY", None)
    os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        health.get_settings.cache_clear()
        reported = _providers()
        assert reported["configured"]["gemini"] is True
        assert reported["configured"]["openai"] is False

        # The whole point: no key, no prefix, not even a length. A masked
        # key is still most of a key, and a length names the provider.
        printed = repr(reported)
        assert "sk-secret-value-do-not-print" not in printed
        assert "secret" not in printed
        for fragment in ("sk-", "key=", str(len("sk-secret-value-do-not-print"))):
            assert fragment not in printed, f"{fragment!r} leaked into /health"
    finally:
        os.environ.pop("GEMINI_API_KEY", None)
        health.get_settings.cache_clear()


def test_it_says_when_review_cannot_be_independent():
    import os

    import app.routes.health as health
    from app.routes.health import _providers

    os.environ["GEMINI_API_KEY"] = "g"
    os.environ.pop("OPENAI_API_KEY", None)
    os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        health.get_settings.cache_clear()
        one = _providers()
        assert one["independent_review"] is False
        assert "not independent" in one["means"]

        os.environ["OPENAI_API_KEY"] = "o"
        health.get_settings.cache_clear()
        two = _providers()
        assert two["independent_review"] is True
        assert "different model" in two["means"]
    finally:
        for name in ("GEMINI_API_KEY", "OPENAI_API_KEY"):
            os.environ.pop(name, None)
        health.get_settings.cache_clear()
