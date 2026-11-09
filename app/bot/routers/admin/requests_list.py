from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts
from app.bot.callbacks import RequestCB
from app.bot.filters import IsAdmin
from app.bot.keyboards.menu import main_menu
from app.bot.keyboards.requests import cancel_kb
from app.bot.routers.admin.helpers import render_list
from app.bot.routers.client.helpers import callback_message, edit_or_answer
from app.bot.states import AdminInput
from app.db.models import User
from app.services import validation


async def show_requests(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    await state.update_data(flt="", query=None)
    text, markup = await render_list(session, user, state, 0)
    await message.answer(text, reply_markup=markup)


async def ask_search(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(AdminInput.search)
    await message.answer(texts.ASK_SEARCH, reply_markup=cancel_kb())


async def got_search(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    try:
        query = validation.validate_search_query(message.text)
    except validation.InvalidInput as exc:
        await message.answer(str(exc))
        return
    await state.set_state(None)
    await state.update_data(query=query, flt="")
    text, markup = await render_list(session, user, state, 0)
    # Первое сообщение возвращает главное меню вместо клавиатуры «Отмена».
    await message.answer(texts.SEARCH_DONE, reply_markup=main_menu(user.role))
    await message.answer(text, reply_markup=markup)


async def set_filter(
    callback: CallbackQuery, callback_data: RequestCB, state: FSMContext, session: AsyncSession, user: User
) -> None:
    await callback.answer()
    await state.update_data(flt=callback_data.status, query=None)
    target = callback_message(callback)
    if target is None:
        return
    text, markup = await render_list(session, user, state, 0)
    await edit_or_answer(target, text, markup)


async def change_page(
    callback: CallbackQuery, callback_data: RequestCB, state: FSMContext, session: AsyncSession, user: User
) -> None:
    await callback.answer()
    target = callback_message(callback)
    if target is None:
        return
    text, markup = await render_list(session, user, state, callback_data.page)
    await edit_or_answer(target, text, markup)


def build_router() -> Router:
    router = Router(name="requests_list")
    router.message.filter(IsAdmin())
    router.callback_query.filter(IsAdmin())
    # Кнопки меню раньше шагов FSM: они выходят из любого текстового шага.
    router.message.register(show_requests, F.text == texts.BTN_REQUESTS)
    router.message.register(ask_search, F.text == texts.BTN_SEARCH)
    router.message.register(got_search, AdminInput.search, ~F.text.in_(texts.ADMIN_MENU_BUTTONS))
    router.callback_query.register(set_filter, RequestCB.filter(F.action == "filter"))
    router.callback_query.register(change_page, RequestCB.filter(F.action == "list"))
    return router
