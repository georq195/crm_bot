from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Comment


async def add(
    session: AsyncSession, request_id: int, author_id: int, text: str, now: datetime
) -> Comment:
    comment = Comment(request_id=request_id, author_id=author_id, text=text, created_at=now)
    session.add(comment)
    await session.flush()
    return comment


async def list_for_request(session: AsyncSession, request_id: int) -> list[Comment]:
    result = await session.scalars(
        select(Comment).where(Comment.request_id == request_id).order_by(Comment.id)
    )
    return list(result)
