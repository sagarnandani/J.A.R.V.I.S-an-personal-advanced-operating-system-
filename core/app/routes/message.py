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

from app import (
    attachments,
    audit,
    claims,
    facts,
    memory,
    offer,
    status,
    system_control,
)
from app.auth import CurrentUser, get_current_user
from app.budget import estimate_cost_inr, estimate_shadow_inr
from app.config import Settings, get_settings
from app.llm import ProviderUnavailable, get_provider
from app.llm import preference
from app import browse
from app.models import OpenInBrowser, MessageRequest, MessageResponse, Offer

logger = logging.getLogger("jarvis.message")

router = APIRouter()


@router.post("/v1/message", response_model=MessageResponse)
async def send_message(
    body: MessageRequest,
    background: BackgroundTasks,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> MessageResponse:
    if not body.text.strip() and not body.attachment_id:
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

    # Is anything actually running? The guard below needs to know, because
    # "it is still being researched" is a fabrication when nothing is and
    # a plain report when something is, and correcting the second would
    # make the guard the one telling the untruth.
    async def _live() -> dict:
        try:
            from app import activity

            return await activity.snapshot()
        except Exception:  # noqa: BLE001
            return {}

    stopped, history, known_facts, status_line, live = await asyncio.gather(
        system_control.is_stopped(), _recall(), _facts(), _status(), _live()
    )
    in_flight = bool(live.get("busy"))

    if stopped:
        raise HTTPException(
            status_code=503,
            detail="JARVIS is currently stopped (Emergency Stop is on). "
            "No requests are being processed. Turn it off via "
            "POST /v1/admin/emergency-stop to resume.",
        )

    # "Open YouTube." Resolved from his own words, before a model is
    # chosen and before one is called -- so it is instant, costs nothing,
    # and works on a deployment with no key configured at all.
    #
    # Not for a message carrying a document: "open the attached file" is
    # about the attachment, not about a website.
    opening = None if body.attachment_id else browse.read(body.text)

    # Which intelligence he asked for, if he asked. This decides WHO does
    # the work and nothing else: permissions come from the registry and
    # the runtime, and a preference cannot widen any of them.
    wanted = preference.read(body.text)
    provider = None
    try:
        if opening is None:
            provider = get_provider(settings, wanted)
    except ProviderUnavailable as exc:
        # Said, never substituted. A hard choice that quietly became a
        # different model would turn a deliberate instruction into a
        # suggestion, and he would have no way of knowing.
        raise HTTPException(
            status_code=503,
            detail=(
                f"You asked for {exc.provider.title()} and I cannot use it: "
                f"{exc.why} Nothing has been run. Say which provider to use "
                f"instead, or drop the restriction and I will choose."
            ),
        ) from exc

    # A document the owner attached, fenced as material rather than added
    # to the instructions. Everything about why that fence is where it is
    # lives in app/attachments.py; the short version is that JARVIS can
    # change its own code now, so a file that could command it would be a
    # path from something on a phone to something on the server.
    attached = None
    asked = body.text.strip()
    if body.attachment_id:
        attached = await attachments.get(body.attachment_id)
        if attached is None:
            raise HTTPException(status_code=404, detail="No such attachment.")
        if not asked:
            asked = "I have attached a document. What does it say?"
        asked = f"{attachments.as_material(attached)}\n\n{asked}"

    model_started = time.perf_counter()
    if opening is not None:
        # A canned result rather than a separate return path, so the
        # exchange is stored, audited and costed exactly like any other.
        # A shortcut that skipped those would make "open YouTube" the one
        # thing JARVIS does that leaves no trace.
        from types import SimpleNamespace

        result = SimpleNamespace(
            text=opening.said, input_tokens=0, output_tokens=0,
            model="none", provider="jarvis",
        )
        outcome = "success"
        model_ms = 0
    else:
        try:
            memory_context = _context(known_facts, status_line)
            result = await provider.complete(asked, history, memory_context)
            outcome = "success"
            model_ms = int((time.perf_counter() - model_started) * 1000)
        except Exception as exc:
            # Graceful degradation (architecture doc, section L): say
            # plainly that the model call failed, don't fabricate a
            # response.
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
    reply_text, objective, kind = offer.split(result.text)
    proposal = await offer.build(objective, kind) if objective else None
    if objective and proposal is None:
        # Marked, but nothing registered can take it. It used to be
        # dropped quietly, which meant the owner read a sentence saying
        # work was coming, saw no card, and had nothing on screen telling
        # them why. Logged AND said.
        logger.info("Ignoring an offer nothing can act on: %s", objective[:120])
        reply_text = claims.nothing_registered(reply_text, kind)
    else:
        # A reply that claims to be searching, drafting or displaying
        # something, with no offer behind it, is describing work that is
        # not going to happen. The prompt says not to; this is what
        # catches it when the prompt does not.
        reply_text, _claimed = claims.correct(
            reply_text, offered=proposal is not None, in_flight=in_flight
        )
        # And whether or not anything was corrected, a reply that talks
        # about the machinery gets the true state underneath it. Stating
        # what is running needs no judgement and is never wrong, which is
        # more than can be said for deciding whether a sentence is a lie.
        reply_text = claims.with_state(reply_text, live.get("said", ""))

    # A soft preference that could not be met is reported rather than
    # left to be noticed in a metadata line. He asked for one thing and
    # got another; that is worth a sentence.
    if (wanted.provider and wanted.mode == preference.SOFT
            and result.provider != wanted.provider):
        reply_text = (
            f"{reply_text.rstrip()}\n\n"
            f"You preferred {wanted.provider.title()}; it was not available, "
            f"so {result.provider.title()} answered instead."
        )

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
    # What it would have cost on a paid model. Never reported as spend --
    # it exists so "cost Rs.0, therefore return infinite" stops being the
    # only thing the economics can say.
    shadow_inr = estimate_shadow_inr(
        result.input_tokens, result.output_tokens, settings, provider=result.provider
    )

    # Recording the exchange and recording the audit row have nothing to
    # say to each other, so they go at once. Everything here happens after
    # the answer already exists, which is the worst place to spend time:
    # the owner is watching a spinner while JARVIS files paperwork.
    # What goes into conversation memory is what the OWNER said, named
    # with the document rather than containing it. The attachment is
    # already on record; putting twenty thousand characters of it into the
    # recent-turns window would push out everything else that was said and
    # be replayed on every message afterwards.
    remembered = body.text.strip()
    if attached:
        label = f"[attached: {attached['filename']}]"
        remembered = f"{label} {remembered}".strip() if remembered else label

    (user_memory_id, reply_memory_id), audit_log_id = await asyncio.gather(
        memory.store_exchange(remembered, reply_text),
        audit.log_audit(
            actor="system",
            action="llm_message_exchange",
            category="low_risk",
            approved_by=None,  # auto-approved: 'drafting'/'research' default to auto
            outcome=outcome,
            cost=cost_inr,
            shadow_cost=shadow_inr,
        ),
    )

    # Learning happens after the reply has been handed over, so the second
    # model call it needs never becomes time the owner spends waiting.
    if settings.memory_facts_enabled:
        background.add_task(
            _learn_quietly,
            provider,
            remembered,
            reply_text,
            user_memory_id,
            settings.memory_facts_per_exchange,
        )

    # Marked only now, after the reply exists. Marking when the briefing
    # was built would mean a message that failed on the way had silently
    # consumed the news it was carrying.
    if status_line and "Work finished since" in status_line:
        background.add_task(status.mark_seen)

    total_ms = int((time.perf_counter() - started) * 1000)
    if attached:
        background.add_task(
            attachments.note_outcome, attached["id"],
            {"read_at": str(user_memory_id), "offer": bool(proposal),
             "objective": objective or None},
        )

    return MessageResponse(
        reply=reply_text,
        offer=Offer(**proposal) if proposal else None,
        open=OpenInBrowser(**opening.as_detail()) if opening else None,
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
