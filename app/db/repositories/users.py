from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Role, User


async def get_or_create(
    session: AsyncSession, tg_id: int, username: str | None, full_name: str, role: Role
) -> User:
    user = await session.scalar(select(User).where(User.tg_id == tg_id))
    if user is None:
        user = User(tg_id=tg_id, username=username, full_name=full_name, role=role)
        session.add(user)
    else:
        user.username = username
        user.full_name = full_name
        user.role = role
    await session.flush()
    return user
