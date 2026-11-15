"""Usage metering and plan entitlements (audio minutes per calendar month, UTC)."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import UsageRecord, User


@dataclass(frozen=True)
class UsageSummary:
    period_start: datetime
    used_seconds: float
    limit_seconds: float

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.limit_seconds - self.used_seconds)


def period_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def plan_limit_seconds(settings: Settings, plan: str) -> float:
    # Single trial plan for now; paid plans come with the billing slice.
    return float(settings.trial_minutes_per_month * 60)


async def summary(db: AsyncSession, settings: Settings, user: User) -> UsageSummary:
    start = period_start()
    used = (
        await db.execute(
            select(func.coalesce(func.sum(UsageRecord.quantity), 0.0)).where(
                UsageRecord.user_id == user.id,
                UsageRecord.kind == "audio_seconds",
                UsageRecord.created_at >= start,
            )
        )
    ).scalar_one()
    return UsageSummary(start, float(used), plan_limit_seconds(settings, user.plan))
