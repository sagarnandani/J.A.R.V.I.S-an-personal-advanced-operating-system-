"""POST /v1/message -- the Stage 0 end-to-end loop.

Text in -> one LLM call -> text out, with the exchange recorded as two
provenance-tagged memories and one audit log row. No agents, no tool use,
no orchestration -- see Stage 0 Build Brief section 4 for what's
deliberately not here yet.
"""
import logging

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

    if await system_control.is_stopped():
        raise HTTPException(
            status_code=503,
            detail="JARVIS is currently stopped (Emergency Stop is on). "
            "No requests are being processed. Turn it off via "
            "POST /v1/admin/emergency-stop to resume.",
        )

    provider = get_provider(settings)

    # What JARVIS remembers of the conversation so far. Read BEFORE this
    # message is stored, so the model is not handed the very thing it is
    # being asked to answer.
    history = []
    if settings.memory_recall_enabled:
        history = await memory.recall_turns(
            limit=settings.memory_recall_turns,
            max_chars=settings.memory_recall_max_chars,
        )

    try:
        result = await provider.complete(body.text, history)
        outcome = "success"
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
    user_memory_id = await memory.store_memory(
        content=body.text, category="episodic", origin="stated", confidence=1.0
    )
    reply_memory_id = await memory.store_memory(
        content=result.text, category="episodic", origin="retrieved", confidence=1.0
    )
    await memory.link_memories(user_memory_id, reply_memory_id)

    # Priced against whichever provider actually answered -- which may not
    # be the configured primary, if it failed and the fallback took over.
    cost_inr = estimate_cost_inr(
        result.input_tokens, result.output_tokens, settings, provider=result.provider
    )
    audit_log_id = await audit.log_audit(
        actor="system",
        action="llm_message_exchange",
        category="low_risk",
        approved_by=None,  # auto-approved: 'drafting'/'research' default to auto
        outcome=outcome,
        cost=cost_inr,
    )

    return MessageResponse(
        reply=result.text,
        user_memory_id=user_memory_id,
        reply_memory_id=reply_memory_id,
        audit_log_id=audit_log_id,
        provider=result.provider,
        model=result.model,
        recalled_turns=len(history),
    )
