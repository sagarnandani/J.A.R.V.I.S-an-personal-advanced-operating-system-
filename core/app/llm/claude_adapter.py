"""Claude provider adapter -- the only place the `anthropic` SDK is imported."""
from anthropic import AsyncAnthropic

from app.llm.base import LLMProvider, LLMResult

JARVIS_SYSTEM_PROMPT = (
    "You are JARVIS, a personal AI operating system. This is Stage 0: a "
    "plain request/response loop with no tools, no agents, and no memory "
    "recall yet. Respond helpfully and briefly."
)


class ClaudeAdapter(LLMProvider):
    def __init__(self, api_key: str, model: str) -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    async def complete(self, message: str) -> LLMResult:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=JARVIS_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": message}],
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
