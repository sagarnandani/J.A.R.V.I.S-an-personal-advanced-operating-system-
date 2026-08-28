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

# Failures that cannot possibly be caused by a thinking budget, so retrying
# without one would just burn a second call against a quota or a bad key.
#
# Deliberately short, and deliberately a DENY-list. Everything not named
# here gets the retry, because being wrong in that direction costs one
# extra call, and being wrong in the other direction breaks every message.
_NOT_ABOUT_THINKING = (
    "api_key_invalid",
    "permission_denied",
    "unauthenticated",
    "resource_exhausted",
    "quota",
    "429",
    "not_found",  # a retired or misspelled model name
)


def _is_definitely_not_about_thinking(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _NOT_ABOUT_THINKING)




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
            if not self._thinking_supported or _is_definitely_not_about_thinking(exc):
                raise

            # Retry WITHOUT the thinking setting, on almost any failure.
            #
            # The first version of this only retried when the error
            # mentioned "thinking" -- and Google's actual refusal says
            # nothing of the sort. It is "400 INVALID_ARGUMENT. Request
            # contains an invalid argument." So the guard never fired and a
            # speed setting broke every message.
            #
            # The lesson is about which way round to guess. Listing the
            # errors worth retrying means every error I failed to imagine
            # breaks JARVIS. Listing the few that are definitely NOT about
            # this setting means an unfamiliar error costs one extra call
            # and still works. Same uncertainty, opposite failure.
            logger.warning(
                "The model rejected the request with a thinking budget set "
                "(%s). Retrying without it.",
                exc,
            )
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=contents,
                config=self._config(False),
            )
            # Only now is it proven: the same request worked without the
            # setting, so the setting was the problem. A retry that also
            # failed would say nothing, and its error is raised instead.
            logger.warning(
                "This model will not accept GEMINI_THINKING_BUDGET, so it is "
                "off for the rest of this process. Replies may be slower and "
                "cost more. Set GEMINI_THINKING_BUDGET=-1 to stop asking."
            )
            self._thinking_supported = False

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
