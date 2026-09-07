"""Request/response schemas (Pydantic) for the API."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel


class MessageRequest(BaseModel):
    text: str


class Offer(BaseModel):
    """Work JARVIS is proposing, which has not happened and costs nothing.

    Present only when the reply's own knowledge was not good enough and
    there is a registered agent that could do better. Accepting it is a
    separate request -- nothing here has run.
    """

    objective: str
    # In words, not a number, so "I have no measurement yet" can be said
    # rather than dressed up as a figure.
    cost_note: str
    typical_cost_inr: float | None = None


class MessageResponse(BaseModel):
    reply: str
    # None for the great majority of messages: chat stays chat.
    offer: Offer | None = None
    user_memory_id: UUID
    reply_memory_id: UUID
    audit_log_id: UUID
    provider: str
    model: str
    # How many past turns JARVIS was given for this answer. Reported so
    # memory is something you can see working rather than infer from the
    # answer sounding right -- an assistant that has silently stopped
    # remembering still produces plausible replies.
    recalled_turns: int
    recalled_facts: int
    # Where the time went, in milliseconds. `model_ms` is waiting on the
    # provider; `our_ms` is everything JARVIS itself did (database reads
    # and writes). Split because they have completely different fixes, and
    # guessing which one is slow wastes effort on the wrong one.
    model_ms: int
    our_ms: int
    total_ms: int


class MemoryOut(BaseModel):
    id: UUID
    content: str
    category: str
    origin: str
    confidence: float
    created_at: datetime
    expires_at: datetime | None
    related_memory_ids: list[UUID]


class AuditLogOut(BaseModel):
    id: UUID
    actor: str
    action: str
    category: str
    approved_by: str | None
    outcome: str | None
    cost: Decimal | None
    created_at: datetime


class BudgetStatus(BaseModel):
    month: str
    spend_inr: Decimal
    ceiling_inr: Decimal
    percent_used: float
    status: str  # ok | warn_50 | warn_80 | exceeded
    note: str


class EmergencyStopRequest(BaseModel):
    stop: bool


class EmergencyStopStatus(BaseModel):
    emergency_stop: bool


# --- forgetting ---
#
# The irreversible operations require the owner to type a confirmation
# phrase. A button alone is one mis-tap away from erasing everything
# JARVIS knows, and the Stage 0 brief's ground rule is explicit: nothing
# destructive without flagging it clearly first.


class ConfirmDestructive(BaseModel):
    confirm: str


class MemoryActionResult(BaseModel):
    ok: bool
    affected: int
    reversible: bool
    message: str
