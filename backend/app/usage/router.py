from datetime import datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.auth.deps import DB, CurrentUser
from app.usage.service import count_this_period, summary

router = APIRouter(tags=["usage"])


class UsageOut(BaseModel):
    period_start: datetime
    used_seconds: float
    limit_seconds: float
    remaining_seconds: float
    copilot_requests_used: int
    copilot_requests_limit: int


@router.get("/usage", response_model=UsageOut)
async def get_usage(request: Request, user: CurrentUser, db: DB) -> UsageOut:
    settings = request.app.state.settings
    s = await summary(db, settings, user)
    asks = await count_this_period(db, user, "copilot_request")
    return UsageOut(
        copilot_requests_used=int(asks),
        copilot_requests_limit=settings.copilot_requests_per_month,
        period_start=s.period_start,
        used_seconds=round(s.used_seconds, 2),
        limit_seconds=s.limit_seconds,
        remaining_seconds=round(s.remaining_seconds, 2),
    )
