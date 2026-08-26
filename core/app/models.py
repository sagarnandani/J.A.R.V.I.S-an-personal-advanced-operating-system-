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
