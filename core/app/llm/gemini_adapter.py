"""Google Gemini provider adapter -- the only place the `google-genai` SDK is imported."""
import logging

from google import genai
from google.genai import types

from app.llm.base import JARVIS_SYSTEM_PROMPT, LLMProvider, LLMResult

logger = logging.getLogger("jarvis.llm.gemini")


class GeminiAdapter(LLMProvider):
    def __init__(self, api_key: str, model: str) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model

    async def complete(self, message: str) -> LLMResult:
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=message,
            config=types.GenerateContentConfig(
                system_instruction=JARVIS_SYSTEM_PROMPT,
                max_output_tokens=1024,
            ),
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
