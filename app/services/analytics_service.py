from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Request, Status, StatusHistory
from app.timeutil import local_day_start_utc


@dataclass
class Stats:
    total: int
    done: int
    to_in_progress_rate: float | None
    to_done_rate: float | None
    avg_processing: timedelta | None


@dataclass
class SummaryCounts:
    new_24h: int
    in_progress: int
    done_24h: int
    stale_new: int


def period_bounds(period: str, now: datetime, tz: str) -> tuple[datetime | None, datetime | None]:
    """Границы периода в naive UTC: date_from включительно, верхняя граница открыта (None)."""
    if period == "today":
        return local_day_start_utc(now, tz), None
    if period == "7d":
        return now - timedelta(days=7), None
    if period == "30d":
        return now - timedelta(days=30), None
    if period == "all":
        return None, None
    raise ValueError(f"Unknown period: {period}")


def _in_range(column, date_from: datetime | None, date_to: datetime | None) -> list:
    conditions = []
    if date_from is not None:
        conditions.append(column >= date_from)
    if date_to is not None:
        conditions.append(column < date_to)
    return conditions


def _reached(status: Status):
    return exists().where(
        StatusHistory.request_id == Request.id, StatusHistory.new_status == status
    )


async def compute(session: AsyncSession, date_from: datetime | None, date_to: datetime | None) -> Stats:
    created_in_period = _in_range(Request.created_at, date_from, date_to)
    closed_in_period = and_(
        Request.status == Status.done,
        Request.closed_at.is_not(None),
        *_in_range(Request.closed_at, date_from, date_to),
    )

    total, reached_progress, reached_done = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(_reached(Status.in_progress)),
                func.count().filter(_reached(Status.in_progress), _reached(Status.done)),
            ).select_from(Request).where(*created_in_period)
        )
    ).one()

    done = await session.scalar(select(func.count()).select_from(Request).where(closed_in_period))

    # Среднее считаем в Python: SQLite не умеет вычитать даты в интервал.
    rows = (
        await session.execute(select(Request.created_at, Request.closed_at).where(closed_in_period))
    ).all()
    avg = sum((closed - created for created, closed in rows), timedelta()) / len(rows) if rows else None

    return Stats(
        total=total,
        done=done,
        to_in_progress_rate=reached_progress / total if total else None,
        to_done_rate=reached_done / reached_progress if reached_progress else None,
        avg_processing=avg,
    )


async def daily_summary_counts(session: AsyncSession, now: datetime, stale_after_hours: int) -> SummaryCounts:
    day_ago = now - timedelta(hours=24)
    stale_before = now - timedelta(hours=stale_after_hours)

    async def count(*conditions) -> int:
        return await session.scalar(select(func.count()).select_from(Request).where(*conditions))

    return SummaryCounts(
        new_24h=await count(Request.created_at >= day_ago, Request.created_at <= now),
        in_progress=await count(Request.status == Status.in_progress),
        done_24h=await count(
            Request.status == Status.done, Request.closed_at >= day_ago, Request.closed_at <= now
        ),
        stale_new=await count(Request.status == Status.new, Request.created_at <= stale_before),
    )
