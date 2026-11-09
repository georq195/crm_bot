import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import RequestCB
from app.bot.texts import fit_message

logger = logging.getLogger(__name__)


class TelegramSender:
    """Реализация Sender поверх aiogram Bot. Без parse_mode: текст уходит как есть."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send(self, tg_id: int, text: str, open_request_id: int | None = None) -> bool:
        markup = None
        if open_request_id is not None:
            builder = InlineKeyboardBuilder()
            builder.button(text="Открыть", callback_data=RequestCB(action="open", id=open_request_id))
            markup = builder.as_markup()
        try:
            await self._bot.send_message(tg_id, fit_message(text), reply_markup=markup)
        except TelegramAPIError as exc:
            logger.warning("Не удалось отправить сообщение пользователю %s: %s", tg_id, exc)
            return False
        return True
