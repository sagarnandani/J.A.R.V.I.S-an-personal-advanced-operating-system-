from fastapi import APIRouter, Depends

from app.auth import CurrentUser, get_current_user
from app.budget import get_budget_snapshot
from app.config import Settings, get_settings
from app.models import BudgetStatus

router = APIRouter()


@router.get("/v1/budget", response_model=BudgetStatus)
async def budget_status(
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> BudgetStatus:
    snapshot = await get_budget_snapshot(settings)
    return BudgetStatus(
        month=snapshot.month,
        spend_inr=snapshot.spend_inr,
        ceiling_inr=snapshot.ceiling_inr,
        percent_used=snapshot.percent_used,
        status=snapshot.status,
        note=snapshot.note,
    )
