from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts
from app.bot.callbacks import PeriodCB
from app.bot.filters import IsAdmin
from app.bot.keyboards.requests import period_kb
from app.bot.routers.client.helpers import callback_message, edit_or_answer
from app.config import Settings
from app.services import analytics_service
from app.timeutil import utcnow

STATS_KIND = "stats"


async def choose_period(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(texts.ANALYTICS_CHOOSE_PERIOD, reply_markup=period_kb(STATS_KIND))


async def show_stats(
    callback: CallbackQuery, callback_data: PeriodCB, session: AsyncSession, settings: Settings
) -> None:
    target = callback_message(callback)
    if callback_data.period not in texts.PERIOD_LABELS:
        await callback.answer(texts.STALE_BUTTON, show_alert=True)
        return
    await callback.answer()
    if target is None:
        return
    date_from, date_to = analytics_service.period_bounds(callback_data.period, utcnow(), settings.timezone)
    stats = await analytics_service.compute(session, date_from, date_to)
    text = texts.format_stats(stats, texts.PERIOD_LABELS[callback_data.period])
    await edit_or_answer(target, text, period_kb(STATS_KIND))


def build_router() -> Router:
    router = Router(name="analytics")
    router.message.filter(IsAdmin())
    router.callback_query.filter(IsAdmin())
    router.message.register(choose_period, F.text == texts.BTN_ANALYTICS)
    router.callback_query.register(show_stats, PeriodCB.filter(F.kind == STATS_KIND))
    return router
