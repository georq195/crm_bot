from datetime import date, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Request, Status, StatusHistory


async def add(
    session: AsyncSession,
    user_id: int,
    client_name: str,
    contact: str,
    description: str,
    preferred_contact_date: date,
    search_text: str,
    now: datetime,
) -> Request:
    request = Request(
        user_id=user_id,
        client_name=client_name,
        contact=contact,
        description=description,
        preferred_contact_date=preferred_contact_date,
        search_text=search_text,
        created_at=now,
        updated_at=now,
    )
    session.add(request)
    await session.flush()
    return request


async def get(session: AsyncSession, request_id: int) -> Request | None:
    return await session.get(Request, request_id)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def list_page(
    session: AsyncSession,
    *,
    user_id: int | None = None,
    status: Status | None = None,
    query: str | None = None,
    offset: int = 0,
    limit: int = 5,
) -> tuple[list[Request], int]:
    conditions = []
    if user_id is not None:
        conditions.append(Request.user_id == user_id)
    if status is not None:
        conditions.append(Request.status == status)
    if query:
        pattern = f"%{_escape_like(query.lower())}%"
        conditions.append(Request.search_text.like(pattern, escape="\\"))

    total = await session.scalar(select(func.count()).select_from(Request).where(*conditions))
    result = await session.scalars(
        select(Request)
        .where(*conditions)
        .order_by(Request.created_at.desc(), Request.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(result), total or 0


async def list_all(
    session: AsyncSession,
    *,
    status: Status | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> list[Request]:
    stmt = select(Request)
    if status is not None:
        stmt = stmt.where(Request.status == status)
    if created_from is not None:
        stmt = stmt.where(Request.created_at >= created_from)
    if created_to is not None:
        stmt = stmt.where(Request.created_at < created_to)
    result = await session.scalars(stmt.order_by(Request.id))
    return list(result)


async def add_history(
    session: AsyncSession,
    request_id: int,
    old_status: Status | None,
    new_status: Status,
    changed_by: int,
    now: datetime,
) -> None:
    session.add(
        StatusHistory(
            request_id=request_id,
            old_status=old_status,
            new_status=new_status,
            changed_by=changed_by,
            changed_at=now,
        )
    )
    await session.flush()


async def delete(session: AsyncSession, request_id: int) -> bool:
    request = await session.get(Request, request_id)
    if request is None:
        return False
    await session.delete(request)
    await session.flush()
    return True


async def list_due_for_reminder(
    session: AsyncSession, created_before: datetime, remind_before: datetime
) -> list[Request]:
    result = await session.scalars(
        select(Request)
        .where(
            Request.status == Status.new,
            Request.created_at <= created_before,
            or_(Request.last_reminded_at.is_(None), Request.last_reminded_at <= remind_before),
        )
        .order_by(Request.created_at, Request.id)
    )
    return list(result)
