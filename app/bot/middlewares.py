from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.db.models import Role
from app.db.repositories import users as users_repo


def resolve_role(tg_id: int, admin_ids: list[int]) -> Role:
    return Role.admin if tg_id in admin_ids else Role.client


class DbSessionMiddleware(BaseMiddleware):
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self._session_factory() as session:
            data["session"] = session
            return await handler(event, data)


class UserMiddleware(BaseMiddleware):
    def __init__(self, admin_ids: list[int]) -> None:
        self._admin_ids = admin_ids

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = getattr(event, "from_user", None)
        if tg_user is None:
            return await handler(event, data)

        session = data["session"]
        user = await users_repo.get_or_create(
            session,
            tg_id=tg_user.id,
            username=tg_user.username,
            full_name=tg_user.full_name,
            role=resolve_role(tg_user.id, self._admin_ids),
        )
        await session.commit()
        data["user"] = user
        return await handler(event, data)
