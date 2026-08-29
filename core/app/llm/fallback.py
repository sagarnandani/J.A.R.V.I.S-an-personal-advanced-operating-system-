"""Automatic failover between providers.

Architecture doc, section L: "if a model provider is down, JARVIS should
say so plainly and either fall back to an alternate model or pause that
category of work -- never silently produce degraded output pretending
nothing's wrong."

This is the "fall back to an alternate model" half. The result still
reports which provider actually answered, so a fallback is visible in the
response and in the audit log rather than being hidden.
"""
import logging

from app.llm.base import LLMProvider, LLMResult, Turn

logger = logging.getLogger("jarvis.llm.fallback")


class FallbackProvider(LLMProvider):
    def __init__(self, primary: LLMProvider, secondary: LLMProvider) -> None:
        self._primary = primary
        self._secondary = secondary

    async def complete(
        self,
        message: str,
        history: list[Turn] | None = None,
        memory_context: str | None = None,
    ) -> LLMResult:
        try:
            return await self._primary.complete(message, history, memory_context)
        except Exception as primary_error:
            logger.warning(
                "Primary model provider failed (%s); trying the fallback provider.",
                primary_error,
            )
            try:
                result = await self._secondary.complete(message, history, memory_context)
            except Exception as secondary_error:
                # Both are down. Report both causes -- knowing only about
                # the second failure would send you debugging the wrong
                # provider.
                raise RuntimeError(
                    f"Both model providers failed. "
                    f"Primary: {primary_error}. Fallback: {secondary_error}."
                ) from secondary_error

            logger.info("Fallback provider '%s' answered successfully.", result.provider)
            return result
