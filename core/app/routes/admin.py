"""Owner-only admin actions. Currently just Emergency Stop.

Every deployment has exactly one authenticated identity (the owner), so
`get_current_user` already restricts this whole router -- no separate
"is this user an admin" check is needed at Stage 0.
"""
from fastapi import APIRouter, Depends

from app import audit, system_control
from app.auth import CurrentUser, get_current_user
from app.models import EmergencyStopRequest, EmergencyStopStatus

router = APIRouter()


@router.get("/v1/admin/emergency-stop", response_model=EmergencyStopStatus)
async def get_emergency_stop(
    user: CurrentUser = Depends(get_current_user),
) -> EmergencyStopStatus:
    return EmergencyStopStatus(emergency_stop=await system_control.is_stopped())


@router.post("/v1/admin/emergency-stop", response_model=EmergencyStopStatus)
async def set_emergency_stop(
    body: EmergencyStopRequest,
    user: CurrentUser = Depends(get_current_user),
) -> EmergencyStopStatus:
    await system_control.set_stopped(body.stop)
    # Toggling the kill switch is itself worth a permanent record --
    # high_risk regardless of which direction it's flipped.
    await audit.log_audit(
        actor=f"user:{user.uid}",
        action=f"emergency_stop set to {body.stop}",
        category="high_risk",
        approved_by=f"user:{user.uid}",
        outcome="success",
        cost=None,
    )
    return EmergencyStopStatus(emergency_stop=body.stop)
