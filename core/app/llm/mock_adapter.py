"""Stand-in used when no model provider has a usable key.

This is the "graceful degradation" behaviour the architecture doc asks for
(section L): if a model provider isn't available, JARVIS says so plainly
rather than silently failing or pretending. It lets the rest of the system
(routing, memory, audit, budget) be built, run, and demonstrated end-to-end
before the owner has added a real API key.

The reply names the key that is actually missing. An earlier version always
said `ANTHROPIC_API_KEY`, which stopped being true when Gemini became the
default provider -- so the one message the owner sees when their key isn't
working was pointing them at the wrong setting to fix.
"""
from app.llm.base import LLMProvider, LLMResult, Turn

# Which environment variable to name, per provider.
_KEY_NAMES = {
    "gemini": "GEMINI_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
}


class MockAdapter(LLMProvider):
    def __init__(self, intended_provider: str | None = None) -> None:
        self.intended_provider = (intended_provider or "").strip().lower()

    def _missing_key_hint(self) -> str:
        key = _KEY_NAMES.get(self.intended_provider)
        if key:
            return f"no {key} is configured on this deployment"
        return "no model provider key is configured on this deployment"

    async def complete(
        self, message: str, history: list[Turn] | None = None
    ) -> LLMResult:
        # Says how much history it was handed, so a recall problem is
        # visible even with no provider key configured -- otherwise the
        # only way to check recall works is to spend money on it.
        recalled = len(history or [])
        reply = (
            f"[JARVIS mock response -- {self._missing_key_hint()}, so no real "
            f"model was called; {recalled} earlier turn(s) recalled] "
            f"You said: {message!r}"
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
