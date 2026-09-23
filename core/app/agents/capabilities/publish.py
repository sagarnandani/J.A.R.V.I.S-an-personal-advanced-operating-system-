"""media.publish -- the last step the chain never had.

Research, strategy, a script, fact-checking, review, the owner's yes --
and then nothing. Every gate was built and the final step was missing,
so the whole chain produced things that could only be published by
copying and pasting them somewhere else.

**It holds PUBLISH, which is in ALWAYS_APPROVED.** So the runtime stops
and asks before anything goes out, every time, with the exact text in
front of him. Posting without being asked is possible -- section 20 of
the brief allows "an explicitly configured low-risk automation policy" --
but that is a thing the owner turns on deliberately, in one place, and
never something an agent can arrange for itself.

**It posts what was reviewed, or what he wrote.** Not what a model
produced a moment ago. Given a piece id, the text comes from the
approved record; given text directly, that text is his. A capability
that took free text from another agent would let anything that can start
a task put words on his professional profile, and every gate upstream
would be decoration.

**It returns evidence.** The id LinkedIn gave the post and a link to it,
recorded against the piece. "I posted it" is precisely the sentence
section 33 says must never be taken on trust.
"""
import logging
from decimal import Decimal

from app.agents import registry
from app.agents.schemas import (
    AgentError,
    AgentResult,
    AgentSpec,
    Handoff,
    Lifecycle,
    ModelTier,
    Permission,
)
from app.config import get_settings
from app.social import linkedin

logger = logging.getLogger("jarvis.capabilities.publish")

SPEC = AgentSpec(
    capability="media.publish",
    name="Publish it",
    description=(
        "Puts an approved piece on LinkedIn, and records the link it came "
        "back with."
    ),
    domain="media",
    task_types=("publish", "media"),
    tools=("linkedin",),
    # PUBLISH is what makes the runtime stop and ask. READ_MEMORY so the
    # approved text can be read back. Nothing else: it posts one thing
    # that was already written and checked.
    permissions=frozenset({Permission.PUBLISH, Permission.READ_MEMORY}),
    model_tiers=(ModelTier.CHEAP,),
    status=Lifecycle.ACTIVE,
    config={"scopes": ["working"], "context_chars": 400},
    max_cost_inr=Decimal("1.00"),
)


async def _text_for(handoff: Handoff) -> tuple[str, str | None]:
    """(what to post, which piece it came from).

    A piece id is the ordinary route: the text is read from the record
    the owner approved, so what goes out is what he said yes to and not
    whatever an agent has in hand now.
    """
    inputs = handoff.inputs or {}
    constraints = handoff.constraints or {}

    piece_id = inputs.get("piece_id") or constraints.get("piece_id")
    if piece_id:
        from app.media import records

        piece = await records.get(piece_id)
        if piece is None:
            raise AgentError(f"There is no piece {piece_id}.", retryable=False)
        if piece["state"] != "approved":
            raise AgentError(
                f"That piece is '{piece['state']}', not approved. Only "
                f"something you have said yes to gets published.",
                retryable=False)
        package = piece.get("package") or {}
        if not isinstance(package, dict):
            # A row whose package is not a mapping. Said plainly rather
            # than surfacing as an AttributeError, which tells the owner
            # nothing and looks like JARVIS breaking.
            raise AgentError(
                f"Piece {piece_id} has no readable text: its package is a "
                f"{type(package).__name__}, not a set of fields.",
                retryable=False)
        text = (package.get("post") or package.get("body")
                or package.get("script") or "")
        if not text.strip():
            raise AgentError(
                "That piece has no text to post.", retryable=False)
        return text, str(piece_id)

    # His own words, handed in directly.
    text = str(inputs.get("text") or constraints.get("text") or "")
    if not text.strip():
        raise AgentError(
            "Nothing to publish. Give me an approved piece, or the text.",
            retryable=False)
    return text, None


async def run(handoff: Handoff, choice) -> AgentResult:
    settings = get_settings()
    text, piece_id = await _text_for(handoff)

    where = str((handoff.constraints or {}).get("to", "linkedin")).lower()
    if where != "linkedin":
        raise AgentError(
            f"JARVIS can only post to LinkedIn so far, not {where!r}.",
            retryable=False)

    posted = await linkedin.post(text, settings)

    if piece_id:
        from app.db import execute

        await execute(
            "UPDATE content_pieces SET state = 'published', "
            "published_at = now(), published_to = $2, published_id = $3, "
            "published_url = $4 WHERE id = $1::uuid",
            piece_id, posted["platform"], posted["id"], posted["url"])

    return AgentResult(
        output=(f"Posted to LinkedIn."
                + (f" {posted['url']}" if posted["url"] else "")),
        # Not 1.0. The post went out and LinkedIn said so; whether it
        # reads well is not something this can know.
        confidence=0.9,
        # The link IS the evidence. Without it "did that go out" is
        # answerable only by opening LinkedIn and looking.
        evidence=[posted["url"]] if posted["url"] else [],
        assumptions=[
            f"Posted {posted['characters']:,} characters to LinkedIn.",
            (f"From the approved piece {piece_id}." if piece_id
             else "From the text you gave me."),
        ],
        unresolved=([] if posted["url"] else
                    ["LinkedIn accepted it but returned no id, so there is "
                     "no link to show. Check your feed."]),
        next_action="",
        model_used="none",
        tokens_in=0, tokens_out=0,
        cost_inr=Decimal(0),
    )


async def install() -> None:
    await registry.register(SPEC)
    registry.implement(SPEC.capability, run)
