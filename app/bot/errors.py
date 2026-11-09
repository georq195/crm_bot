"""Глобальный обработчик ошибок: пользователь не остаётся с «вечным» спиннером."""
import logging

from aiogram.types import ErrorEvent

from app.bot import texts

logger = logging.getLogger(__name__)


async def on_error(event: ErrorEvent) -> bool:
    logger.exception("Необработанная ошибка при обработке обновления", exc_info=event.exception)
    update = event.update
    try:
        if update.callback_query is not None:
            await update.callback_query.answer(texts.ERROR_GENERIC, show_alert=True)
        elif update.message is not None:
            await update.message.answer(texts.ERROR_GENERIC)
    except Exception:
        logger.exception("Не удалось сообщить пользователю об ошибке")
    return True
