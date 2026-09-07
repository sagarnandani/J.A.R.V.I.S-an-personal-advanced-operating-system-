"""POST /v1/message -- the Stage 0 end-to-end loop.

Text in -> one LLM call -> text out, with the exchange recorded as two
provenance-tagged memories and one audit log row. No agents, no tool use,
no orchestration -- see Stage 0 Build Brief section 4 for what's
deliberately not here yet.
"""
import asyncio
import logging
import time

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from app import audit, facts, memory, offer, status, system_control
from app.auth import CurrentUser, get_current_user
from app.budget import estimate_cost_inr
from app.config import Settings, get_settings
from app.llm import get_provider
from app.models import MessageRequest, MessageResponse, Offer

logger = logging.getLogger("jarvis.message")

router = APIRouter()


@router.post("/v1/message", response_model=MessageResponse)
async def send_message(
    body: MessageRequest,
    background: BackgroundTasks,
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

    # Long-term facts are fetched alongside the rest -- relevance-matched
    # against this message, so something said months ago comes back when
    # it is relevant, long after the conversation it came from scrolled
    # out of the recent window.
    async def _facts():
        if not settings.memory_facts_enabled:
            return []
        return await facts.recall_facts(
            body.text,
            limit=settings.memory_facts_limit,
            max_chars=settings.memory_facts_max_chars,
        )

    # The status briefing rides along with the rest -- one more query in
    # the same batch, so it costs no extra waiting.
    async def _status():
        try:
            return await status.briefing(settings)
        except Exception:  # noqa: BLE001 - a briefing is never worth a failure
            return ""

    stopped, history, known_facts, status_line = await asyncio.gather(
        system_control.is_stopped(), _recall(), _facts(), _status()
    )

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
        memory_context = _context(known_facts, status_line)
        result = await provider.complete(body.text, history, memory_context)
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

    # The model marks a message it judges worth real work, on the end of
    # the reply it was already writing. Split before anything else touches
    # the text: the marker must never reach the owner, and must never be
    # stored as though JARVIS had said it.
    reply_text, objective = offer.split(result.text)
    proposal = await offer.build(objective) if objective else None
    if objective and proposal is None:
        # Marked, but nothing registered can take it. Dropped quietly --
        # an offer JARVIS cannot honour is worse than none.
        logger.info("Ignoring an offer nothing can act on: %s", objective[:120])

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
        memory.store_exchange(body.text, reply_text),
        audit.log_audit(
            actor="system",
            action="llm_message_exchange",
            category="low_risk",
            approved_by=None,  # auto-approved: 'drafting'/'research' default to auto
            outcome=outcome,
            cost=cost_inr,
        ),
    )

    # Learning happens after the reply has been handed over, so the second
    # model call it needs never becomes time the owner spends waiting.
    if settings.memory_facts_enabled:
        background.add_task(
            _learn_quietly,
            provider,
            body.text,
            reply_text,
            user_memory_id,
            settings.memory_facts_per_exchange,
        )

    total_ms = int((time.perf_counter() - started) * 1000)
    return MessageResponse(
        reply=reply_text,
        offer=Offer(**proposal) if proposal else None,
        user_memory_id=user_memory_id,
        reply_memory_id=reply_memory_id,
        audit_log_id=audit_log_id,
        provider=result.provider,
        model=result.model,
        recalled_turns=len(history),
        recalled_facts=len(known_facts),
        # Reported so "it feels slow" can be answered with a number
        # instead of a guess -- and so it is obvious whether the time went
        # to the model or to JARVIS's own work.
        model_ms=model_ms,
        total_ms=total_ms,
        our_ms=total_ms - model_ms,
    )


async def _learn_quietly(
    provider, user_text: str, reply_text: str, source_memory_id, max_facts: int
) -> None:
    """Extract long-term facts, and never let failing at it matter.

    The owner already has their answer by the time this runs. If writing
    down what was learned goes wrong -- a model hiccup, malformed JSON,
    the database blinking -- the right outcome is a log line, not a lost
    conversation or an error the owner cannot act on.
    """
    try:
        learned, retired = await facts.learn_from_exchange(
            provider, user_text, reply_text, source_memory_id, max_facts
        )
        if learned or retired:
            logger.info("Long-term memory: learned %d, retired %d", learned, retired)
            await audit.log_audit(
                actor="system",
                action="memory_learn",
                category="low_risk",
                outcome=f"learned {learned}, retired {retired}",
            )
    except Exception as exc:  # noqa: BLE001 - never fatal, by design
        logger.warning("Could not extract long-term facts: %s", exc)


def _context(known_facts: list[str], status_line: str) -> str | None:
    """What JARVIS knows about its owner, plus where things stand now.

    Both go into the system prompt rather than the conversation: they are
    standing knowledge, not something anybody just said.
    """
    parts = []
    if known_facts:
        parts.append("\n".join(f"- {f}" for f in known_facts))
    if status_line:
        parts.append(status_line)
    return "\n\n".join(parts) or None
