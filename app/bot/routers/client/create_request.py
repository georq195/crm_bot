import logging
from datetime import date, timedelta

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts
from app.bot.callbacks import RequestCB
from app.bot.keyboards.menu import main_menu
from app.bot.keyboards.requests import cancel_kb, confirm_kb, date_kb
from app.bot.routers.client.helpers import callback_message, drop_markup
from app.bot.sender import TelegramSender
from app.bot.states import CreateRequest
from app.config import Settings
from app.db.models import User
from app.services import notification_service, request_service, validation
from app.timeutil import to_local, utcnow

logger = logging.getLogger(__name__)

_DATE_FORMAT = "%d.%m.%Y"


def _today(settings: Settings) -> date:
    return to_local(utcnow(), settings.timezone).date()


async def start_create(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(CreateRequest.name)
    name = message.from_user.full_name if message.from_user else ""
    await message.answer(texts.ASK_NAME, reply_markup=cancel_kb(name))


async def got_name(message: Message, state: FSMContext) -> None:
    try:
        name = validation.validate_name(message.text)
    except validation.InvalidInput as exc:
        await message.answer(str(exc))
        return
    await state.update_data(name=name)
    await state.set_state(CreateRequest.contact)
    await message.answer(texts.ASK_CONTACT, reply_markup=cancel_kb())


async def got_contact(message: Message, state: FSMContext) -> None:
    try:
        contact = validation.validate_contact(message.text)
    except validation.InvalidInput as exc:
        await message.answer(str(exc))
        return
    await state.update_data(contact=contact)
    await state.set_state(CreateRequest.description)
    await message.answer(texts.ASK_DESCRIPTION, reply_markup=cancel_kb())


async def got_description(message: Message, state: FSMContext) -> None:
    try:
        description = validation.validate_description(message.text)
    except validation.InvalidInput as exc:
        await message.answer(str(exc))
        return
    await state.update_data(description=description)
    await state.set_state(CreateRequest.date)
    await message.answer(texts.ASK_DATE, reply_markup=date_kb())


async def _to_confirm(target: Message, state: FSMContext, preferred: date) -> None:
    await state.update_data(preferred=preferred.isoformat())
    await state.set_state(CreateRequest.confirm)
    data = await state.get_data()
    await target.answer(
        texts.format_confirm(data["name"], data["contact"], data["description"], preferred.strftime(_DATE_FORMAT)),
        reply_markup=confirm_kb(),
    )


async def got_date_text(message: Message, state: FSMContext, settings: Settings) -> None:
    try:
        preferred = validation.parse_preferred_date(message.text, _today(settings))
    except validation.InvalidInput as exc:
        await message.answer(str(exc))
        return
    await _to_confirm(message, state, preferred)


async def got_date_button(callback: CallbackQuery, callback_data: RequestCB, state: FSMContext, settings: Settings) -> None:
    await callback.answer()
    target = callback_message(callback)
    if target is None:
        return
    preferred = _today(settings) + timedelta(days=1 if callback_data.status == "tomorrow" else 0)
    await _to_confirm(target, state, preferred)


async def confirm_hint(message: Message) -> None:
    await message.answer(texts.USE_BUTTONS)


async def submit(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    bot: Bot,
    settings: Settings,
) -> None:
    # состояние сбрасываем сразу, чтобы повторный клик не создал дубль
    data = await state.get_data()
    await state.clear()
    target = callback_message(callback)
    required = ("name", "contact", "description", "preferred")
    if target is None or any(key not in data for key in required):
        await callback.answer(texts.REQUEST_ALREADY_HANDLED)
        return
    await callback.answer()
    await drop_markup(target)
    try:
        request = await request_service.create_request(
            session,
            user,
            data["name"],
            data["contact"],
            data["description"],
            date.fromisoformat(data["preferred"]),
        )
    except Exception:
        logger.exception("Не удалось сохранить заявку пользователя %s", callback.from_user.id)
        # возвращаем шаг подтверждения, чтобы можно было нажать «Отправить» ещё раз
        await state.set_state(CreateRequest.confirm)
        await state.set_data(data)
        await target.answer(texts.REQUEST_SAVE_FAILED, reply_markup=confirm_kb())
        return
    await target.answer(texts.REQUEST_CREATED.format(id=request.id), reply_markup=main_menu(user.role))
    await notification_service.notify_new_request(
        TelegramSender(bot), request, user, settings.admin_ids, settings.timezone
    )


async def cancel_inline(callback: CallbackQuery, state: FSMContext, user: User) -> None:
    await state.clear()
    await callback.answer()
    target = callback_message(callback)
    if target is not None:
        await drop_markup(target)
        await target.answer(texts.CANCELLED, reply_markup=main_menu(user.role))


async def already_handled(callback: CallbackQuery) -> None:
    await callback.answer(texts.REQUEST_ALREADY_HANDLED, show_alert=True)


def build_router() -> Router:
    router = Router(name="create_request")
    router.message.register(start_create, F.text == texts.BTN_NEW_REQUEST)
    router.message.register(got_name, CreateRequest.name)
    router.message.register(got_contact, CreateRequest.contact)
    router.message.register(got_description, CreateRequest.description)
    router.message.register(got_date_text, CreateRequest.date)
    router.message.register(confirm_hint, CreateRequest.confirm)
    router.callback_query.register(
        got_date_button, RequestCB.filter(F.action == "date"), CreateRequest.date
    )
    router.callback_query.register(submit, RequestCB.filter(F.action == "submit"), CreateRequest.confirm)
    router.callback_query.register(cancel_inline, RequestCB.filter(F.action == "cancel"), CreateRequest.confirm)
    # кнопки без подходящего состояния (повторное нажатие, старое сообщение)
    router.callback_query.register(already_handled, RequestCB.filter(F.action.in_({"submit", "cancel"})))
    return router
