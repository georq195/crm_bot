from datetime import date, timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts
from app.bot.callbacks import RequestCB
from app.bot.filters import IsClient
from app.bot.keyboards.menu import main_menu
from app.bot.keyboards.requests import cancel_kb, client_card_kb, date_kb, edit_fields_kb
from app.bot.routers.client.helpers import build_list, callback_message, card_text, edit_or_answer
from app.bot.states import EditRequest
from app.config import Settings
from app.db.models import User
from app.services import request_service, validation
from app.timeutil import to_local, utcnow

_DATE_FIELD = "preferred_contact_date"
_VALIDATORS = {
    "client_name": validation.validate_name,
    "contact": validation.validate_contact,
    "description": validation.validate_description,
}


def _today(settings: Settings) -> date:
    return to_local(utcnow(), settings.timezone).date()


async def _back_to_list(target: Message, session: AsyncSession, user: User, page: int) -> None:
    text, markup = await build_list(session, user, page)
    await edit_or_answer(target, text, markup)


async def choose_field(
    callback: CallbackQuery,
    callback_data: RequestCB,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    settings: Settings,
) -> None:
    target = callback_message(callback)
    try:
        request = await request_service.get_request_for_actor(session, callback_data.id, user)
    except request_service.RequestNotFound:
        await state.clear()
        await callback.answer(texts.REQUEST_NOT_FOUND, show_alert=True)
        if target is not None:
            await _back_to_list(target, session, user, callback_data.page)
        return
    if not request_service.can_edit(request, user):
        # Кнопка со старой карточки: заявку уже взяли в работу. Не ведём клиента по шагам зря.
        await callback.answer(texts.EDIT_NOT_ALLOWED, show_alert=True)
        if target is not None:
            await edit_or_answer(
                target, card_text(request, settings.timezone), client_card_kb(request, callback_data.page)
            )
        return
    await callback.answer()
    await state.clear()
    await state.set_state(EditRequest.field)
    if target is not None:
        await edit_or_answer(
            target, texts.EDIT_CHOOSE_FIELD.format(id=callback_data.id), edit_fields_kb(callback_data.id, callback_data.page)
        )


async def ask_value(
    callback: CallbackQuery,
    callback_data: RequestCB,
    state: FSMContext,
    session: AsyncSession,
    user: User,
) -> None:
    target = callback_message(callback)
    field = callback_data.status
    try:
        if field not in texts.EDIT_PROMPTS:
            raise request_service.RequestNotFound(callback_data.id)
        await request_service.get_request_for_actor(session, callback_data.id, user)
    except request_service.RequestNotFound:
        await state.clear()
        await callback.answer(texts.REQUEST_NOT_FOUND, show_alert=True)
        if target is not None:
            await _back_to_list(target, session, user, callback_data.page)
        return
    await callback.answer()
    await state.set_state(EditRequest.value)
    await state.update_data(request_id=callback_data.id, field=field, page=callback_data.page)
    if target is None:
        return
    await target.answer(texts.EDIT_PROMPTS[field], reply_markup=cancel_kb())
    if field == _DATE_FIELD:
        await target.answer(texts.BTN_TODAY + " / " + texts.BTN_TOMORROW + ":", reply_markup=date_kb())


async def _save(
    target: Message, state: FSMContext, session: AsyncSession, user: User, settings: Settings, value
) -> None:
    data = await state.get_data()
    await state.clear()
    request_id, field, page = data["request_id"], data["field"], data.get("page", 0)
    # читаем до вызова сервиса: при ошибке сессия откатывается и объект протухает
    role = user.role
    try:
        request = await request_service.edit_request(session, request_id, user, **{field: value})
    except request_service.EditNotAllowed:
        await target.answer(texts.EDIT_NOT_ALLOWED, reply_markup=main_menu(role))
        return
    except request_service.RequestNotFound:
        await target.answer(texts.REQUEST_NOT_FOUND, reply_markup=main_menu(role))
        await session.refresh(user)
        text, markup = await build_list(session, user, page)
        await target.answer(text, reply_markup=markup)
        return
    await target.answer(texts.REQUEST_UPDATED, reply_markup=main_menu(role))
    await target.answer(card_text(request, settings.timezone), reply_markup=client_card_kb(request, page))


async def got_value(message: Message, state: FSMContext, session: AsyncSession, user: User, settings: Settings) -> None:
    field = (await state.get_data()).get("field")
    try:
        if field == _DATE_FIELD:
            value = validation.parse_preferred_date(message.text, _today(settings))
        elif field in _VALIDATORS:
            value = _VALIDATORS[field](message.text)
        else:  # состояние без данных: безопасно выходим
            await state.clear()
            await message.answer(texts.REQUEST_NOT_FOUND, reply_markup=main_menu(user.role))
            return
    except validation.InvalidInput as exc:
        await message.answer(str(exc))
        return
    await _save(message, state, session, user, settings, value)


async def got_date_button(
    callback: CallbackQuery,
    callback_data: RequestCB,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    settings: Settings,
) -> None:
    target = callback_message(callback)
    if (await state.get_data()).get("field") != _DATE_FIELD or target is None:
        await callback.answer(texts.REQUEST_ALREADY_HANDLED)
        return
    await callback.answer()
    value = _today(settings) + timedelta(days=1 if callback_data.status == "tomorrow" else 0)
    await _save(target, state, session, user, settings, value)


def build_router() -> Router:
    router = Router(name="edit_request")
    router.message.filter(IsClient())
    router.callback_query.filter(IsClient())
    router.callback_query.register(choose_field, RequestCB.filter(F.action == "edit"))
    router.callback_query.register(ask_value, RequestCB.filter(F.action == "ef"))
    router.message.register(got_value, EditRequest.value)
    router.callback_query.register(got_date_button, RequestCB.filter(F.action == "date"), EditRequest.value)
    return router
