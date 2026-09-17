"""OpenAI provider adapter -- the only place the `openai` SDK is imported.

Deliberately the same shape as claude_adapter.py. Three providers that
each look different are three things to debug; three that look the same
are one.

One difference worth knowing: OpenAI puts the system prompt in the
messages list rather than in its own field, so `system_prompt_with` is
prepended as a `system` turn instead of passed separately.
"""
import logging

from openai import AsyncOpenAI

from app.llm.base import LLMProvider, LLMResult, Turn, system_prompt_with

logger = logging.getLogger("jarvis.llm.openai")


class OpenAIAdapter(LLMProvider):
    def __init__(self, api_key: str, model: str) -> None:
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model

    async def complete(
        self,
        message: str,
        history: list[Turn] | None = None,
        memory_context: str | None = None,
    ) -> LLMResult:
        messages = [{"role": "system", "content": system_prompt_with(memory_context)}]
        # Our role names are already "user" and "assistant", which is what
        # OpenAI uses, so history passes through unchanged.
        messages += [{"role": t.role, "content": t.text} for t in history or []]
        messages.append({"role": "user", "content": message})

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_completion_tokens=1024,
            )
        except Exception as exc:  # noqa: BLE001
            # OpenAI retires model names, and the 404 names what is wrong
            # without saying what to do about it. Said here instead, once,
            # because the fix is a one-line environment change and the
            # alternative is reading a stack trace on a phone.
            text = str(exc)
            if "model" in text.lower() and ("not found" in text.lower()
                                            or "does not exist" in text.lower()):
                raise RuntimeError(
                    f"OpenAI does not have a model called '{self._model}'. "
                    f"Set OPENAI_MODEL in your .env to one your account can "
                    f"use, then restart. Nothing else needs to change. "
                    f"(OpenAI said: {text[:300]})"
                ) from exc
            raise

        choice = response.choices[0]
        usage = response.usage
        return LLMResult(
            text=choice.message.content or "",
            # Absent on some responses; reported as zero rather than
            # guessed, so a cost of zero means "not measured" and never a
            # number that was invented here.
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            model=self._model,
            provider="openai",
        )
