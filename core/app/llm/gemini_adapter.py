"""Google Gemini provider adapter -- the only place the `google-genai` SDK is imported."""
import logging

from google import genai
from google.genai import types

from app.llm.base import (
    ASSISTANT,
    JARVIS_SYSTEM_PROMPT,
    LLMProvider,
    LLMResult,
    Turn,
)

logger = logging.getLogger("jarvis.llm.gemini")


class GeminiAdapter(LLMProvider):
    def __init__(self, api_key: str, model: str, thinking_budget: int = 0) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._thinking_budget = thinking_budget
        # Not every model lets thinking be configured, and I cannot check
        # which from here. So it is attempted, and if the model rejects it
        # the flag flips and every later call goes without -- rather than
        # every message failing over a speed setting.
        self._thinking_supported = True

    @staticmethod
    def _to_contents(message: str, history: list[Turn] | None) -> list:
        """The conversation in Gemini's shape.

        Gemini calls the assistant side "model" rather than "assistant",
        which is the only reason this mapping exists -- the rest of JARVIS
        should not have to know that.
        """
        contents = []
        for turn in history or []:
            contents.append(
                types.Content(
                    role="model" if turn.role == ASSISTANT else "user",
                    parts=[types.Part.from_text(text=turn.text)],
                )
            )
        contents.append(
            types.Content(role="user", parts=[types.Part.from_text(text=message)])
        )
        return contents

    def _config(self, with_thinking_setting: bool) -> types.GenerateContentConfig:
        thinking = None
        if with_thinking_setting and self._thinking_budget >= 0:
            thinking = types.ThinkingConfig(thinking_budget=self._thinking_budget)
        return types.GenerateContentConfig(
            system_instruction=JARVIS_SYSTEM_PROMPT,
            max_output_tokens=1024,
            thinking_config=thinking,
        )

    async def complete(
        self, message: str, history: list[Turn] | None = None
    ) -> LLMResult:
        contents = self._to_contents(message, history)
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=contents,
                config=self._config(self._thinking_supported),
            )
        except Exception as exc:
            # Only a rejected thinking setting is retried, and only once.
            # Anything else is a real failure and must surface as one --
            # swallowing it here would turn "your API key is wrong" into a
            # mysterious silence.
            if not (self._thinking_supported and "thinking" in str(exc).lower()):
                raise
            logger.warning(
                "This model would not accept a thinking budget (%s); continuing "
                "without one. Replies may be slower. Set GEMINI_THINKING_BUDGET "
                "to -1 to stop asking.",
                exc,
            )
            self._thinking_supported = False
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=contents,
                config=self._config(False),
            )

        text = response.text or ""

        usage = response.usage_metadata
        input_tokens = getattr(usage, "prompt_token_count", None) or 0
        # Gemini's reasoning models think before answering, and those
        # "thinking" tokens are billed as output even though they never
        # appear in the reply. Counting only the visible answer would
        # quietly under-report what this call actually costs. Read
        # defensively because a model that doesn't think omits the field
        # entirely rather than reporting zero.
        visible_output = getattr(usage, "candidates_token_count", None) or 0
        thinking_output = getattr(usage, "thoughts_token_count", None) or 0

        return LLMResult(
            text=text,
            input_tokens=input_tokens,
            output_tokens=visible_output + thinking_output,
            model=self._model,
            provider="gemini",
        )
