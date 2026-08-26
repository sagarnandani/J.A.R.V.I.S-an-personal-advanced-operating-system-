"""Fallback adapter used when no ANTHROPIC_API_KEY is configured.

This is the "graceful degradation" behaviour the architecture doc asks for
(section L): if a model provider isn't available, JARVIS says so plainly
rather than silently failing or pretending. It lets the rest of the system
(routing, memory, audit, budget) be built, run, and demonstrated end-to-end
before the owner has added a real API key.
"""
from app.llm.base import LLMProvider, LLMResult


class MockAdapter(LLMProvider):
    async def complete(self, message: str) -> LLMResult:
        reply = (
            "[JARVIS mock response -- no ANTHROPIC_API_KEY is configured on "
            f"this deployment, so no real model was called] You said: {message!r}"
        )
        # Fake but proportional token counts, so the budget math downstream
        # is exercised the same way it would be with a real call.
        return LLMResult(
            text=reply,
            input_tokens=max(1, len(message) // 4),
            output_tokens=max(1, len(reply) // 4),
            model="mock",
            provider="mock",
        )
