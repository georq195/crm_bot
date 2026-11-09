from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts
from app.bot.callbacks import RequestCB
from app.bot.filters import IsClient
from app.bot.keyboards.requests import client_card_kb
from app.bot.routers.client.helpers import build_list, callback_message, card_text, edit_or_answer
from app.config import Settings
from app.db.models import User
from app.services import request_service


async def show_my_requests(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    text, markup = await build_list(session, user, 0)
    await message.answer(text, reply_markup=markup)


async def change_page(callback: CallbackQuery, callback_data: RequestCB, session: AsyncSession, user: User) -> None:
    await callback.answer()
    target = callback_message(callback)
    if target is None:
        return
    text, markup = await build_list(session, user, callback_data.page)
    await edit_or_answer(target, text, markup)


async def open_card(
    callback: CallbackQuery,
    callback_data: RequestCB,
    session: AsyncSession,
    user: User,
    settings: Settings,
) -> None:
    target = callback_message(callback)
    try:
        request = await request_service.get_request_for_actor(session, callback_data.id, user)
    except request_service.RequestNotFound:
        await callback.answer(texts.REQUEST_NOT_FOUND, show_alert=True)
        if target is not None:
            text, markup = await build_list(session, user, callback_data.page)
            await edit_or_answer(target, text, markup)
        return
    await callback.answer()
    if target is not None:
        await edit_or_answer(
            target, card_text(request, settings.timezone), client_card_kb(request, callback_data.page)
        )


def build_router() -> Router:
    router = Router(name="my_requests")
    router.message.filter(IsClient())
    router.callback_query.filter(IsClient())
    router.message.register(show_my_requests, F.text == texts.BTN_MY_REQUESTS)
    router.callback_query.register(change_page, RequestCB.filter(F.action == "list"))
    router.callback_query.register(open_card, RequestCB.filter(F.action == "open"))
    return router
