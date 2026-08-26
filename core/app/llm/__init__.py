"""LLM provider adapters. See base.py for the interface every provider implements."""
from app.config import Settings
from app.llm.base import LLMProvider


def get_provider(settings: Settings) -> LLMProvider:
    if settings.anthropic_api_key:
        from app.llm.claude_adapter import ClaudeAdapter

        return ClaudeAdapter(
            api_key=settings.anthropic_api_key, model=settings.claude_model
        )
    from app.llm.mock_adapter import MockAdapter

    return MockAdapter()
