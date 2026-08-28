"""POST /v1/message -- the Stage 0 end-to-end loop.

Text in -> one LLM call -> text out, with the exchange recorded as two
provenance-tagged memories and one audit log row. No agents, no tool use,
no orchestration -- see Stage 0 Build Brief section 4 for what's
deliberately not here yet.
"""
import asyncio
import logging
import time

from fastapi import APIRouter, Depends, HTTPException

from app import audit, memory, system_control
from app.auth import CurrentUser, get_current_user
from app.budget import estimate_cost_inr
from app.config import Settings, get_settings
from app.llm import get_provider
from app.models import MessageRequest, MessageResponse

logger = logging.getLogger("jarvis.message")

router = APIRouter()


@router.post("/v1/message", response_model=MessageResponse)
async def send_message(
    body: MessageRequest,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> MessageResponse:
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="text must not be empty")

    started = time.perf_counter()

    # The stop flag and the conversation history are independent questions,
    # so they are asked at the same time rather than one after the other.
    # Against a hosted database each is a network round trip, and the owner
    # waits through every one of them.
    #
    # Recalling history before the stop check is confirmed does no harm: it
    # is a read, and if JARVIS turns out to be stopped the result is simply
    # discarded.
    async def _recall():
        if not settings.memory_recall_enabled:
            return []
        return await memory.recall_turns(
            limit=settings.memory_recall_turns,
            max_chars=settings.memory_recall_max_chars,
        )

    stopped, history = await asyncio.gather(system_control.is_stopped(), _recall())

    if stopped:
        raise HTTPException(
            status_code=503,
            detail="JARVIS is currently stopped (Emergency Stop is on). "
            "No requests are being processed. Turn it off via "
            "POST /v1/admin/emergency-stop to resume.",
        )

    provider = get_provider(settings)

    model_started = time.perf_counter()
    try:
        result = await provider.complete(body.text, history)
        outcome = "success"
        model_ms = int((time.perf_counter() - model_started) * 1000)
    except Exception as exc:
        # Graceful degradation (architecture doc, section L): say plainly
        # that the model call failed, don't fabricate a response.
        logger.exception("LLM call failed")
        await audit.log_audit(
            actor="system",
            action="llm_message_exchange",
            category="low_risk",
            outcome=f"error: {exc}",
            cost=None,
        )
        raise HTTPException(
            status_code=502,
            detail=f"The language model provider failed to respond: {exc}",
        ) from exc

    # Provenance: the user's own words are 'stated'. JARVIS's reply is
    # content that came back from the model -- 'retrieved' -- never
    # 'stated' (that word is reserved for what the user told us) and never
    # 'inferred'/'predicted' (Stage 0 adds no interpretation about the
    # user; see memory.py's module docstring).
    # Priced against whichever provider actually answered -- which may not
    # be the configured primary, if it failed and the fallback took over.
    cost_inr = estimate_cost_inr(
        result.input_tokens, result.output_tokens, settings, provider=result.provider
    )

    # Recording the exchange and recording the audit row have nothing to
    # say to each other, so they go at once. Everything here happens after
    # the answer already exists, which is the worst place to spend time:
    # the owner is watching a spinner while JARVIS files paperwork.
    (user_memory_id, reply_memory_id), audit_log_id = await asyncio.gather(
        memory.store_exchange(body.text, result.text),
        audit.log_audit(
            actor="system",
            action="llm_message_exchange",
            category="low_risk",
            approved_by=None,  # auto-approved: 'drafting'/'research' default to auto
            outcome=outcome,
            cost=cost_inr,
        ),
    )

    total_ms = int((time.perf_counter() - started) * 1000)
    return MessageResponse(
        reply=result.text,
        user_memory_id=user_memory_id,
        reply_memory_id=reply_memory_id,
        audit_log_id=audit_log_id,
        provider=result.provider,
        model=result.model,
        recalled_turns=len(history),
        # Reported so "it feels slow" can be answered with a number
        # instead of a guess -- and so it is obvious whether the time went
        # to the model or to JARVIS's own work.
        model_ms=model_ms,
        total_ms=total_ms,
        our_ms=total_ms - model_ms,
    )
