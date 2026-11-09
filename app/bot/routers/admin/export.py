import logging
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts
from app.bot.callbacks import ExportCB, PeriodCB
from app.bot.filters import IsAdmin
from app.bot.keyboards.requests import export_format_kb, export_status_kb, period_kb
from app.bot.routers.client.helpers import callback_message, edit_or_answer
from app.config import Settings
from app.db.models import Status
from app.services import analytics_service, export_service
from app.timeutil import utcnow

logger = logging.getLogger(__name__)

EXPORT_DIR = Path("exports")
FORMATS = ("csv", "xlsx")
STATUS_ALL = "all"


async def choose_format(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(texts.EXPORT_CHOOSE_FORMAT, reply_markup=export_format_kb())


async def choose_period(callback: CallbackQuery, callback_data: ExportCB) -> None:
    """Шаг 1 (формат выбран, период ещё нет) ведёт к периоду."""
    target = callback_message(callback)
    if callback_data.fmt not in FORMATS:
        await callback.answer(texts.STALE_BUTTON, show_alert=True)
        return
    await callback.answer()
    if target is not None:
        await edit_or_answer(target, texts.EXPORT_CHOOSE_PERIOD, period_kb(callback_data.fmt))


async def choose_status(callback: CallbackQuery, callback_data: PeriodCB) -> None:
    target = callback_message(callback)
    if callback_data.period not in texts.PERIOD_LABELS:
        await callback.answer(texts.STALE_BUTTON, show_alert=True)
        return
    await callback.answer()
    if target is not None:
        await edit_or_answer(
            target, texts.EXPORT_CHOOSE_STATUS, export_status_kb(callback_data.kind, callback_data.period)
        )


async def send_export(
    callback: CallbackQuery,
    callback_data: ExportCB,
    session: AsyncSession,
    bot: Bot,
    settings: Settings,
) -> None:
    target = callback_message(callback)
    valid_statuses = {STATUS_ALL, *(s.value for s in Status)}
    if (
        callback_data.status not in valid_statuses
        or callback_data.fmt not in FORMATS
        or callback_data.period not in texts.PERIOD_LABELS
    ):
        await callback.answer(texts.STALE_BUTTON, show_alert=True)
        return
    await callback.answer()
    if target is None:
        return
    status = None if callback_data.status == STATUS_ALL else Status(callback_data.status)
    date_from, date_to = analytics_service.period_bounds(callback_data.period, utcnow(), settings.timezone)
    try:
        path = await export_service.build_export(
            session,
            callback_data.fmt,  # type: ignore[arg-type]
            date_from=date_from,
            date_to=date_to,
            status=status,
            tz=settings.timezone,
            out_dir=EXPORT_DIR,
        )
    except Exception:
        logger.exception("Не удалось сформировать экспорт")
        await target.answer(texts.EXPORT_BUILD_FAILED)
        return
    if path is None:
        await target.answer(texts.EXPORT_NOTHING)
        return
    try:
        await bot.send_document(target.chat.id, FSInputFile(path))
    except TelegramAPIError:
        await target.answer(texts.EXPORT_SEND_FAILED)
    finally:
        path.unlink(missing_ok=True)


def build_router() -> Router:
    router = Router(name="export")
    router.message.filter(IsAdmin())
    router.callback_query.filter(IsAdmin())
    router.message.register(choose_format, F.text == texts.BTN_EXPORT)
    router.callback_query.register(choose_period, ExportCB.filter(F.period == ""))
    router.callback_query.register(choose_status, PeriodCB.filter(F.kind.in_(set(FORMATS))))
    router.callback_query.register(send_export, ExportCB.filter())
    return router
