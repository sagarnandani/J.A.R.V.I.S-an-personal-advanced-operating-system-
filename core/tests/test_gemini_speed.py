"""Gemini's thinking budget, and the retry that keeps it optional.

Gemini's models reason to themselves before answering. That reasoning is
never shown, is billed as output, and is time the owner spends watching a
spinner -- so for conversation it is turned off by default.

Not every model allows that, and I cannot check which from here. So the
adapter asks, and carries on without if refused. That fallback is only
worth having if it works, and it is exactly the kind of path that never
runs in normal use, so it is tested directly.
"""
import pytest

from app.llm.gemini_adapter import GeminiAdapter


class _Usage:
    prompt_token_count = 10
    candidates_token_count = 5
    thoughts_token_count = 0


class _Response:
    text = "hello"
    usage_metadata = _Usage()


class _FakeModels:
    """Stands in for the SDK, recording what configs it was handed."""

    def __init__(self, reject_thinking: bool = False, fail_with: str | None = None):
        self.reject_thinking = reject_thinking
        self.fail_with = fail_with
        self.configs = []

    async def generate_content(self, *, model, contents, config):
        self.configs.append(config)
        if self.fail_with:
            raise RuntimeError(self.fail_with)
        if self.reject_thinking and config.thinking_config is not None:
            raise RuntimeError("400 INVALID_ARGUMENT: thinking is not supported")
        return _Response()


def _adapter(models, thinking_budget: int = 0) -> GeminiAdapter:
    adapter = GeminiAdapter.__new__(GeminiAdapter)
    adapter._client = type("C", (), {"aio": type("A", (), {"models": models})()})()
    adapter._model = "gemini-test"
    adapter._thinking_budget = thinking_budget
    adapter._thinking_supported = True
    return adapter


@pytest.mark.asyncio
async def test_thinking_is_turned_off_by_default():
    models = _FakeModels()
    await _adapter(models, thinking_budget=0).complete("hi")
    assert models.configs[0].thinking_config.thinking_budget == 0


@pytest.mark.asyncio
async def test_a_negative_budget_leaves_the_decision_to_the_model():
    """-1 means "you decide", which is not the same as asking for zero."""
    models = _FakeModels()
    await _adapter(models, thinking_budget=-1).complete("hi")
    assert models.configs[0].thinking_config is None


@pytest.mark.asyncio
async def test_a_model_that_refuses_the_setting_still_answers():
    models = _FakeModels(reject_thinking=True)
    result = await _adapter(models).complete("hi")

    assert result.text == "hello"
    assert len(models.configs) == 2, "should have retried once"
    assert models.configs[1].thinking_config is None


@pytest.mark.asyncio
async def test_it_stops_asking_after_being_refused_once():
    """Otherwise every single message pays for a wasted failed call."""
    models = _FakeModels(reject_thinking=True)
    adapter = _adapter(models)

    await adapter.complete("first")
    await adapter.complete("second")

    assert len(models.configs) == 3, "1 rejected + 1 retry, then 1 clean call"
    assert models.configs[2].thinking_config is None


@pytest.mark.asyncio
async def test_a_real_failure_is_not_swallowed_by_the_retry():
    """The retry must not turn a broken key into a mysterious silence.

    Only a rejected thinking setting is retried. Anything else has to
    surface as the failure it is, with its own message intact.
    """
    models = _FakeModels(fail_with="API_KEY_INVALID: API key not valid")
    with pytest.raises(RuntimeError, match="API_KEY_INVALID"):
        await _adapter(models).complete("hi")
    assert len(models.configs) == 1, "must not retry a real failure"
