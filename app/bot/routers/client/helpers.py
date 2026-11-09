"""Общие вспомогательные функции клиентских роутеров (без бизнес-правил)."""
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts
from app.bot.keyboards.requests import request_list_kb
from app.db.models import Request, User
from app.services import request_service


def callback_message(callback: CallbackQuery) -> Message | None:
    """Сообщение, к которому прикреплена кнопка (None, если оно недоступно)."""
    return callback.message if isinstance(callback.message, Message) else None


async def edit_or_answer(target: Message, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    text = texts.fit_message(text)
    try:
        await target.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc):
            return
        await target.answer(text, reply_markup=markup)


async def drop_markup(target: Message) -> None:
    """Убирает inline-кнопки у сообщения; сбой Telegram не должен ломать сценарий."""
    try:
        await target.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass


async def build_list(
    session: AsyncSession, user: User, page: int
) -> tuple[str, InlineKeyboardMarkup | None]:
    requests, total = await request_service.list_requests(session, user, page=page)
    if total and not requests:  # страница за пределами списка (удалили заявки): последняя страница
        last = (total - 1) // texts.PAGE_SIZE
        requests, total = await request_service.list_requests(session, user, page=last)
        page = last
    if not total:
        return texts.NO_REQUESTS, None
    return texts.MY_REQUESTS_TITLE, request_list_kb(requests, page, total)


def card_text(request: Request, tz: str) -> str:
    return texts.format_request_card(request, [], for_admin=False, tz=tz)
