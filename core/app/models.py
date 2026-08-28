"""Request/response schemas (Pydantic) for the API."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel


class MessageRequest(BaseModel):
    text: str


class MessageResponse(BaseModel):
    reply: str
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
