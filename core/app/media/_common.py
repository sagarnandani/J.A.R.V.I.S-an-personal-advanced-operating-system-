"""Shared machinery for the media capabilities.

Four agents that all do the same shaped thing -- read a briefing, make a
judgement, return structured JSON -- and differ only in the judgement.
Repeating the parsing four times would mean four places for a model's bad
JSON day to become an exception the owner cannot act on.
"""
import json
import logging
import re
from decimal import Decimal
from typing import Any

from app.agents.schemas import AgentError, AgentResult, Handoff

logger = logging.getLogger("jarvis.media")


def parse_json(raw: str) -> dict | None:
    """Read the model's JSON forgivingly, and never raise.

    Same rule as everywhere else in this project: models fence their JSON,
    preface it, or return prose. None means "could not read it", and the
    caller decides whether that is a failure worth retrying.
    """
    text = (raw or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


async def think(prompt: str, what: str) -> tuple[dict, Any]:
    """One model call, returning parsed JSON and the raw result.

    Unreadable JSON raises a retryable error rather than returning
    something empty. A media agent that silently produces nothing looks
    exactly like one that considered the material and had no view, and
    those are very different things to a workflow deciding what to do
    next.
    """
    from app.config import get_settings
    from app.llm import get_provider

    result = await get_provider(get_settings()).complete(prompt)
    data = parse_json(result.text)
    if data is None:
        logger.warning("%s did not return JSON: %.200s", what, result.text)
        raise AgentError(f"{what} did not return readable JSON.", retryable=True)
    return data, result


def brand_of(handoff: Handoff) -> str:
    """Which property this task belongs to.

    Read from the task's own inputs rather than guessed from the
    objective. The Script agent writes in one of two quite different
    voices, and inferring the wrong one produces work that reads as the
    other brand with a different logo.
    """
    from app.media import brands

    asked = str((handoff.inputs or {}).get("brand") or "").strip().lower()
    return asked if asked in brands.BRANDS else brands.AI_MEDIA


def result(output: Any, *, confidence: float, model, **extra) -> AgentResult:
    """The common shape, so cost is priced in one place by the runtime."""
    return AgentResult(
        output=output,
        confidence=confidence,
        model_used=getattr(model, "model", None),
        tokens_in=getattr(model, "input_tokens", 0),
        tokens_out=getattr(model, "output_tokens", 0),
        cost_inr=Decimal(0),   # priced by the runtime, from one price list
        **extra,
    )


def briefing(handoff: Handoff) -> str:
    """What this task was given to work from."""
    parts = []
    if handoff.context:
        parts.append(handoff.context)
    inputs = handoff.inputs or {}
    for key in ("material", "brief", "script", "strategy"):
        if inputs.get(key):
            parts.append(f"{key}:\n{inputs[key]}")
    return "\n\n".join(parts).strip()
