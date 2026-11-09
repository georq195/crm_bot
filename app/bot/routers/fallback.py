"""Запасной роутер: подключается последним и отвечает на всё, что не поймали остальные."""
from aiogram import Router
from aiogram.types import CallbackQuery, Message

from app.bot import texts
from app.bot.keyboards.menu import main_menu
from app.db.models import Role, User


async def stale_callback(callback: CallbackQuery) -> None:
    await callback.answer(texts.STALE_CALLBACK, show_alert=True)


async def unknown_message(message: Message, user: User | None = None) -> None:
    role = user.role if user is not None else Role.client
    await message.answer(texts.UNKNOWN_MESSAGE, reply_markup=main_menu(role))


def build_router() -> Router:
    router = Router(name="fallback")
    router.callback_query.register(stale_callback)
    router.message.register(unknown_message)
    return router
