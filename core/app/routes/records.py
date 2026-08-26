"""Read-only debug endpoints so the owner can verify Stage 0's DoD item:
'visible in memories and audit_log with correct provenance' -- without
needing direct database access.
"""
from fastapi import APIRouter, Depends

from app import audit, memory
from app.auth import CurrentUser, get_current_user
from app.models import AuditLogOut, MemoryOut

router = APIRouter()


@router.get("/v1/memories", response_model=list[MemoryOut])
async def get_memories(
    limit: int = 20, user: CurrentUser = Depends(get_current_user)
) -> list[MemoryOut]:
    rows = await memory.list_recent(limit)
    return [MemoryOut(**dict(r)) for r in rows]


@router.get("/v1/audit", response_model=list[AuditLogOut])
async def get_audit_log(
    limit: int = 20, user: CurrentUser = Depends(get_current_user)
) -> list[AuditLogOut]:
    rows = await audit.list_recent(limit)
    return [AuditLogOut(**dict(r)) for r in rows]
