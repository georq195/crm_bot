from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject

from app.db.models import Role, User


class _RoleFilter(BaseFilter):
    role: Role

    async def __call__(self, event: TelegramObject, user: User | None = None) -> bool:
        return user is not None and user.role == self.role


class IsAdmin(_RoleFilter):
    role: Role = Role.admin


class IsClient(_RoleFilter):
    role: Role = Role.client
