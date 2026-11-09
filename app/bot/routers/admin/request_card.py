from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts
from app.bot.callbacks import RequestCB
from app.bot.filters import IsAdmin
from app.bot.keyboards.menu import main_menu
from app.bot.keyboards.requests import card_kb, cancel_kb, delete_confirm_kb
from app.bot.routers.admin.helpers import card_text, render_list
from app.bot.routers.client.helpers import callback_message, edit_or_answer
from app.bot.sender import TelegramSender
from app.bot.states import AdminInput
from app.config import Settings
from app.db.models import Request, Status, User
from app.labels import STATUS_LABELS
from app.services import notification_service, request_service, validation


async def _stale(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, user: User, page: int
) -> None:
    """Заявка удалена или id устарел: сообщаем и возвращаем в список."""
    await callback.answer(texts.REQUEST_NOT_FOUND, show_alert=True)
    target = callback_message(callback)
    if target is not None:
        await session.refresh(user)  # сервис мог откатить сессию: user протух
        text, markup = await render_list(session, user, state, page)
        await edit_or_answer(target, text, markup)


async def _load(session: AsyncSession, request_id: int, user: User) -> Request | None:
    try:
        return await request_service.get_request_for_actor(session, request_id, user)
    except request_service.RequestNotFound:
        return None


async def _show_card(target: Message, request: Request, page: int, settings: Settings) -> None:
    await edit_or_answer(target, card_text(request, settings.timezone), card_kb(request, page))


async def open_card(
    callback: CallbackQuery,
    callback_data: RequestCB,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    settings: Settings,
) -> None:
    request = await _load(session, callback_data.id, user)
    if request is None:
        await _stale(callback, state, session, user, callback_data.page)
        return
    await callback.answer()
    target = callback_message(callback)
    if target is not None:
        await _show_card(target, request, callback_data.page, settings)


async def change_status(
    callback: CallbackQuery,
    callback_data: RequestCB,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    bot: Bot,
    settings: Settings,
) -> None:
    page = callback_data.page
    # читаем до вызова сервиса: при ошибке сессия откатывается и объект протухает
    admin_tg_id = user.tg_id
    try:
        new_status = Status(callback_data.status)
    except ValueError:
        await _stale(callback, state, session, user, page)
        return
    if await _load(session, callback_data.id, user) is None:
        await _stale(callback, state, session, user, page)
        return
    target = callback_message(callback)
    try:
        request = await request_service.change_status(session, callback_data.id, new_status, user)
    except request_service.InvalidTransition:
        await session.refresh(user)
        current = await _load(session, callback_data.id, user)
        if current is None:  # заявку удалили между проверкой и сменой: один ответ — «не найдена»
            await _stale(callback, state, session, user, page)
            return
        await callback.answer(texts.STATUS_ALREADY_CHANGED, show_alert=True)
        if target is not None:
            await _show_card(target, current, page, settings)
        return
    except request_service.RequestNotFound:
        await _stale(callback, state, session, user, page)
        return
    await callback.answer(texts.STATUS_CHANGED.format(status=STATUS_LABELS[new_status]))
    if target is not None:
        await _show_card(target, request, page, settings)
    await notification_service.notify_status_changed(
        TelegramSender(bot), request, request.author, new_status, admin_tg_id
    )


async def ask_comment(
    callback: CallbackQuery, callback_data: RequestCB, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if await _load(session, callback_data.id, user) is None:
        await _stale(callback, state, session, user, callback_data.page)
        return
    await callback.answer()
    await state.set_state(AdminInput.comment)
    await state.update_data(comment_request_id=callback_data.id, comment_page=callback_data.page)
    target = callback_message(callback)
    if target is not None:
        await target.answer(texts.ASK_COMMENT.format(id=callback_data.id), reply_markup=cancel_kb())


async def got_comment(
    message: Message, state: FSMContext, session: AsyncSession, user: User, settings: Settings
) -> None:
    data = await state.get_data()
    request_id = data.get("comment_request_id")
    page = data.get("comment_page", 0)
    role = user.role
    try:
        text = validation.validate_comment(message.text)
    except validation.InvalidInput as exc:
        await message.answer(str(exc))
        return
    await state.set_state(None)
    await state.update_data(comment_request_id=None)
    try:
        if request_id is None:
            raise request_service.RequestNotFound(0)
        await request_service.add_comment(session, request_id, user, text)
    except request_service.RequestNotFound:
        await session.refresh(user)
        await message.answer(texts.REQUEST_NOT_FOUND, reply_markup=main_menu(role))
        list_text, markup = await render_list(session, user, state, page)
        await message.answer(list_text, reply_markup=markup)
        return
    await message.answer(texts.COMMENT_ADDED, reply_markup=main_menu(role))
    request = await _load(session, request_id, user)
    if request is not None:
        await message.answer(card_text(request, settings.timezone), reply_markup=card_kb(request, page))


async def ask_delete(
    callback: CallbackQuery, callback_data: RequestCB, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if await _load(session, callback_data.id, user) is None:
        await _stale(callback, state, session, user, callback_data.page)
        return
    await callback.answer()
    target = callback_message(callback)
    if target is not None:
        await edit_or_answer(
            target,
            texts.CONFIRM_DELETE.format(id=callback_data.id),
            delete_confirm_kb(callback_data.id, callback_data.page),
        )


async def confirm_delete(
    callback: CallbackQuery, callback_data: RequestCB, state: FSMContext, session: AsyncSession, user: User
) -> None:
    try:
        await request_service.delete_request(session, callback_data.id)
    except request_service.RequestNotFound:
        await _stale(callback, state, session, user, callback_data.page)
        return
    await callback.answer(texts.REQUEST_DELETED.format(id=callback_data.id))
    target = callback_message(callback)
    if target is not None:
        text, markup = await render_list(session, user, state, callback_data.page)
        await edit_or_answer(target, text, markup)


def build_router() -> Router:
    router = Router(name="request_card")
    router.message.filter(IsAdmin())
    router.callback_query.filter(IsAdmin())
    router.callback_query.register(open_card, RequestCB.filter(F.action == "open"))
    router.callback_query.register(change_status, RequestCB.filter(F.action == "status"))
    router.callback_query.register(ask_comment, RequestCB.filter(F.action == "comment"))
    router.callback_query.register(ask_delete, RequestCB.filter(F.action == "delete"))
    router.callback_query.register(confirm_delete, RequestCB.filter(F.action == "delete_ok"))
    # Кнопки меню не должны попадать в текст комментария: они обрабатываются своими хендлерами.
    router.message.register(got_comment, AdminInput.comment, ~F.text.in_(texts.ADMIN_MENU_BUTTONS))
    return router
