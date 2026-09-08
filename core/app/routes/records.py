"""Read-only debug endpoints so the owner can verify Stage 0's DoD item:
'visible in memories and audit_log with correct provenance' -- without
needing direct database access.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app import audit, memory
from app.auth import CurrentUser, get_current_user
from app.models import (
    AuditLogOut,
    ConfirmDestructive,
    MemoryActionResult,
    MemoryOut,
    MoneyIn,
)

# Typed, not tapped. Anything that cannot be undone asks for these words
# in full, so erasing what JARVIS knows can never be one mis-tap on a
# small screen.
CONFIRM_DELETE_ALL = "DELETE EVERYTHING"

router = APIRouter()


@router.get("/v1/memories", response_model=list[MemoryOut])
async def get_memories(
    limit: int = 20,
    include_forgotten: bool = False,
    user: CurrentUser = Depends(get_current_user),
) -> list[MemoryOut]:
    """What JARVIS remembers.

    Forgotten memories are left out unless asked for. They still exist --
    forgetting is reversible -- but showing them here by default would
    make "forget that" look like it had not worked.
    """
    rows = await memory.list_recent(limit, include_forgotten=include_forgotten)
    return [MemoryOut(**dict(r)) for r in rows]


@router.post("/v1/memories/{memory_id}/forget", response_model=MemoryActionResult)
async def forget_one(
    memory_id: UUID, user: CurrentUser = Depends(get_current_user)
) -> MemoryActionResult:
    """Stop recalling one memory. Reversible."""
    affected = await memory.forget_memory(memory_id)
    if not affected:
        raise HTTPException(status_code=404, detail="No memory with that ID.")

    await audit.log_audit(
        actor=f"user:{user.email or user.uid}",
        action="memory_forget",
        category="medium_risk",
        approved_by=f"user:{user.uid}",
        outcome=f"forgot {affected} memories for exchange {memory_id}",
    )
    return MemoryActionResult(
        ok=True,
        affected=affected,
        reversible=True,
        message="Forgotten -- both what you said and JARVIS's reply, since "
        "a reply usually repeats what it is replying to. JARVIS will not "
        "recall this any more. It can still be restored; use 'delete' to "
        "erase it for good.",
    )


@router.post("/v1/memories/{memory_id}/restore", response_model=MemoryActionResult)
async def restore_one(
    memory_id: UUID, user: CurrentUser = Depends(get_current_user)
) -> MemoryActionResult:
    affected = await memory.restore_memory(memory_id)
    if not affected:
        raise HTTPException(status_code=404, detail="No memory with that ID.")

    await audit.log_audit(
        actor=f"user:{user.email or user.uid}",
        action="memory_restore",
        category="low_risk",
        approved_by=f"user:{user.uid}",
        outcome=f"restored {affected} memories for exchange {memory_id}",
    )
    return MemoryActionResult(
        ok=True,
        affected=affected,
        reversible=True,
        message="Restored. JARVIS can recall this exchange again.",
    )


@router.delete("/v1/memories/{memory_id}", response_model=MemoryActionResult)
async def delete_one(
    memory_id: UUID, user: CurrentUser = Depends(get_current_user)
) -> MemoryActionResult:
    """Erase one memory for good.

    Audited as high risk. Not because one memory matters that much, but
    because destroying data is the category of thing that should always
    leave a trace of who did it and when -- including when it was the
    owner, doing it on purpose.
    """
    affected = await memory.delete_memory(memory_id)
    if not affected:
        raise HTTPException(status_code=404, detail="No memory with that ID.")

    await audit.log_audit(
        actor=f"user:{user.email or user.uid}",
        action="memory_delete",
        category="high_risk",
        approved_by=f"user:{user.uid}",
        outcome=f"permanently deleted {affected} memories for exchange {memory_id}",
    )
    return MemoryActionResult(
        ok=True,
        affected=affected,
        reversible=False,
        message=f"Erased {affected} memories permanently -- the message and "
        "its reply. This cannot be undone.",
    )


@router.post("/v1/memories/forget-all", response_model=MemoryActionResult)
async def forget_everything(
    body: ConfirmDestructive, user: CurrentUser = Depends(get_current_user)
) -> MemoryActionResult:
    """Make JARVIS forget the entire conversation. Reversible per memory."""
    count = await memory.forget_all()
    await audit.log_audit(
        actor=f"user:{user.email or user.uid}",
        action="memory_forget_all",
        category="high_risk",
        approved_by=f"user:{user.uid}",
        outcome=f"forgot {count} memories",
    )
    return MemoryActionResult(
        ok=True,
        affected=count,
        reversible=True,
        message=f"JARVIS has forgotten {count} memories and starts fresh. "
        "They still exist and can be restored one at a time, or erased "
        "for good with 'purge'.",
    )


@router.post("/v1/memories/purge", response_model=MemoryActionResult)
async def purge_forgotten(
    body: ConfirmDestructive, user: CurrentUser = Depends(get_current_user)
) -> MemoryActionResult:
    """Erase everything already forgotten. No undo.

    The confirmation phrase has to be typed out. This is the one action
    here that genuinely destroys data in bulk, and a button alone is one
    mis-tap away from it.
    """
    if body.confirm != CONFIRM_DELETE_ALL:
        raise HTTPException(
            status_code=400,
            detail=f"To erase forgotten memories for good, send confirm "
            f"exactly as: {CONFIRM_DELETE_ALL}",
        )

    count = await memory.purge_forgotten()
    await audit.log_audit(
        actor=f"user:{user.email or user.uid}",
        action="memory_purge",
        category="high_risk",
        approved_by=f"user:{user.uid}",
        outcome=f"permanently deleted {count} memories",
    )
    return MemoryActionResult(
        ok=True,
        affected=count,
        reversible=False,
        message=f"Erased {count} forgotten memories for good. This cannot be undone.",
    )


@router.get("/v1/audit", response_model=list[AuditLogOut])
async def get_audit_log(
    limit: int = 20, user: CurrentUser = Depends(get_current_user)
) -> list[AuditLogOut]:
    rows = await audit.list_recent(limit)
    return [AuditLogOut(**dict(r)) for r in rows]


# --- money ------------------------------------------------------------------

@router.get("/v1/money", include_in_schema=False)
async def money_listing(
    limit: int = 20, user: CurrentUser = Depends(get_current_user)
) -> dict:
    from app import money

    return {
        "totals": await money.totals(),
        "recent": await money.recent(min(max(limit, 1), 100)),
    }


@router.post("/v1/money", include_in_schema=False)
async def money_add(
    body: MoneyIn, user: CurrentUser = Depends(get_current_user)
) -> dict:
    """Add a figure by hand.

    Most rows arrive from what the owner says. This is for the times they
    would rather type it, and for correcting one that was misheard.
    """
    from app import money

    row = await money.record(
        body.direction, body.amount_inr, body.what, body.category,
        occurred_on=body.occurred_on, source="stated",
    )
    if row is None:
        raise HTTPException(
            status_code=400,
            detail="A money entry needs a direction ('in' or 'out'), an "
                   "amount above zero, and what it was for.",
        )
    return row


@router.delete("/v1/money/{event_id}", include_in_schema=False)
async def money_remove(
    event_id: UUID, user: CurrentUser = Depends(get_current_user)
) -> dict:
    """Remove a figure outright.

    Deleted rather than hidden, unlike a memory: a ledger that quietly
    keeps a number you told it to drop is one whose totals you cannot
    check against your own bank.
    """
    from app import money

    if not await money.forget(event_id):
        raise HTTPException(status_code=404, detail="No such entry.")
    return {"ok": True}
