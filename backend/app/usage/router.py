from datetime import datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.auth.deps import DB, CurrentUser
from app.usage.service import summary

router = APIRouter(tags=["usage"])


class UsageOut(BaseModel):
    period_start: datetime
    used_seconds: float
    limit_seconds: float
    remaining_seconds: float


@router.get("/usage", response_model=UsageOut)
async def get_usage(request: Request, user: CurrentUser, db: DB) -> UsageOut:
    s = await summary(db, request.app.state.settings, user)
    return UsageOut(
        period_start=s.period_start,
        used_seconds=round(s.used_seconds, 2),
        limit_seconds=s.limit_seconds,
        remaining_seconds=round(s.remaining_seconds, 2),
    )
