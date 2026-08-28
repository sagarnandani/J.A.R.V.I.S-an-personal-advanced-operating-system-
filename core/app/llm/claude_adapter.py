"""Claude provider adapter -- the only place the `anthropic` SDK is imported."""
from anthropic import AsyncAnthropic

from app.llm.base import JARVIS_SYSTEM_PROMPT, LLMProvider, LLMResult, Turn


class ClaudeAdapter(LLMProvider):
    def __init__(self, api_key: str, model: str) -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    async def complete(
        self, message: str, history: list[Turn] | None = None
    ) -> LLMResult:
        # Claude's role names match ours, so the history passes through
        # unchanged. It does require the turns to alternate starting with
        # the user, which normalise_history has already guaranteed.
        messages = [{"role": t.role, "content": t.text} for t in history or []]
        messages.append({"role": "user", "content": message})

        response = await self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=JARVIS_SYSTEM_PROMPT,
            messages=messages,
        )
        text = "".join(
            block.text for block in response.content if block.type == "text"
        )
        return LLMResult(
            text=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=self._model,
            provider="anthropic",
        )
