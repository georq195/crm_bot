"""Сценарные тесты клиентских роутеров: настоящие Update через dp.feed_update, Bot с записывающей сессией."""
from datetime import timedelta

import pytest
from aiogram.methods import SendMessage

from app.bot.callbacks import RequestCB
from app.bot.states import CreateRequest, EditRequest
from app.db.models import Request, Role, Status, User
from app.services import request_service
from app.timeutil import to_local, utcnow
from helpers import ADMIN, CLIENT, NOT_EDITABLE, NOT_FOUND, OTHER, Chatter, all_requests, seed_request


async def test_sticker_on_description_keeps_state_and_asks_for_text(client, bot):
    await client.say("Новая заявка")
    await client.say("Иван")
    await client.say("+79991234567")
    assert await client.state() == CreateRequest.description.state

    await client.sticker()

    assert await client.state() == CreateRequest.description.state
    assert bot.session.texts()[-1] == "Отправьте ответ текстом"


@pytest.mark.parametrize("step", ["name", "contact", "date"])
async def test_sticker_on_other_text_steps_keeps_state(client, bot, step):
    await client.say("Новая заявка")
    if step in ("contact", "date"):
        await client.say("Иван")
    if step == "date":
        await client.say("+79991234567")
        await client.say("Описание")
    expected = getattr(CreateRequest, step)
    assert await client.state() == expected.state

    await client.sticker()

    assert await client.state() == expected.state
    assert bot.session.texts()[-1] == "Отправьте ответ текстом"


async def test_invalid_input_explains_and_stays_on_step(client, bot):
    await client.say("Новая заявка")
    await client.say("Иван")
    await client.say("не телефон")
    assert await client.state() == CreateRequest.contact.state
    assert "Неверный контакт" in bot.session.texts()[-1]


async def test_past_date_is_rejected(client, bot, settings):
    await client.say("Новая заявка")
    await client.say("Иван")
    await client.say("@ivan_petrov")
    await client.say("Описание")
    today = to_local(utcnow(), settings.timezone).date()
    await client.say((today - timedelta(days=1)).strftime("%d.%m.%Y"))
    assert await client.state() == CreateRequest.date.state
    assert bot.session.texts()[-1] == "Дата не может быть в прошлом"


async def test_name_step_offers_telegram_name_and_cancel(client, bot):
    await client.say("Новая заявка")
    markup = bot.session.sent(CLIENT)[-1].reply_markup
    labels = [b.text for row in markup.keyboard for b in row]
    assert "Иван Петров" in labels and "Отмена" in labels


async def test_date_step_offers_today_and_tomorrow(client, bot):
    await client.say("Новая заявка")
    await client.say("Иван")
    await client.say("+79991234567")
    await client.say("Описание")
    markup = bot.session.sent(CLIENT)[-1].reply_markup
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert labels == ["Сегодня", "Завтра"]


async def test_full_flow_creates_one_request_and_notifies_admin(client, bot, session_factory, settings):
    await client.create_until_confirm()
    assert await client.state() == CreateRequest.confirm.state
    assert await all_requests(session_factory) == []

    await client.press(action="submit")

    requests = await all_requests(session_factory)
    assert len(requests) == 1
    request = requests[0]
    assert (request.client_name, request.contact, request.description, request.status) == (
        "Иван Петров", "+79991234567", "Нужна консультация", Status.new,
    )
    assert request.preferred_contact_date == to_local(utcnow(), settings.timezone).date()
    assert await client.state() is None
    to_admin = bot.session.sent(ADMIN)
    assert len(to_admin) == 1 and "Нужна консультация" in to_admin[0].text
    assert to_admin[0].reply_markup.inline_keyboard[0][0].text == "Открыть"
    assert any("отправлена" in t for t in bot.session.texts(CLIENT))
    for call in bot.session.calls:
        # Bot без DefaultBotProperties: parse_mode либо не задан, либо остаётся плейсхолдером Default.
        assert not isinstance(getattr(call, "parse_mode", None), str)


async def test_tomorrow_button_sets_next_day(client, session_factory, settings):
    await client.say("Новая заявка")
    await client.say("Иван")
    await client.say("+79991234567")
    await client.say("Описание")
    await client.press(action="date", status="tomorrow")
    await client.press(action="submit")
    (request,) = await all_requests(session_factory)
    assert request.preferred_contact_date == to_local(utcnow(), settings.timezone).date() + timedelta(days=1)


async def test_double_submit_creates_single_request(client, bot, session_factory):
    await client.create_until_confirm()
    await client.press(action="submit")
    await client.press(action="submit")

    assert len(await all_requests(session_factory)) == 1
    assert len(bot.session.sent(ADMIN)) == 1
    assert "Заявка уже отправлена или отменена" in bot.session.alerts()


async def test_cancel_inline_on_confirm_creates_nothing(client, session_factory):
    await client.create_until_confirm()
    await client.press(action="cancel")
    assert await client.state() is None
    assert await all_requests(session_factory) == []


async def test_text_cancel_on_any_step_clears_state(client):
    await client.say("Новая заявка")
    await client.say("Иван")
    await client.say("Отмена")
    assert await client.state() is None


async def test_notification_failure_does_not_break_flow(client, bot, session_factory):
    original = bot.session.make_request

    async def failing(bot_, method, timeout=None):
        if isinstance(method, SendMessage) and method.chat_id == ADMIN:
            raise RuntimeError("telegram is down")
        return await original(bot_, method, timeout)

    bot.session.make_request = failing
    await client.create_until_confirm()
    await client.press(action="submit")
    assert len(await all_requests(session_factory)) == 1


async def test_my_requests_shows_only_own(client, bot, session_factory):
    own = await seed_request(session_factory, CLIENT, "моё")
    await seed_request(session_factory, OTHER, "чужое")

    await client.say("Мои заявки")

    message = bot.session.sent(CLIENT)[-1]
    buttons = [b for row in message.reply_markup.inline_keyboard for b in row]
    assert [RequestCB.unpack(b.callback_data).id for b in buttons] == [own]


async def test_my_requests_empty(client, bot):
    await client.say("Мои заявки")
    assert bot.session.texts()[-1] == "У вас пока нет заявок"


async def test_my_requests_paginates_by_five(client, bot, session_factory):
    for i in range(7):
        await seed_request(session_factory, CLIENT, f"d{i}")
    await client.say("Мои заявки")
    markup = bot.session.sent(CLIENT)[-1].reply_markup
    rows = markup.inline_keyboard
    assert len(rows) == 6  # 5 заявок + строка пагинации
    assert [b.text for b in rows[-1]] == ["›"]
    await client.press(action="list", page=1)
    edited = bot.session.calls[-2]  # перед ним answerCallbackQuery или после: ищем edit
    edit = [c for c in bot.session.calls if type(c).__name__ == "EditMessageText"][-1]
    assert len(edit.reply_markup.inline_keyboard) == 3  # 2 заявки + «‹»
    assert edited is not None


async def test_open_own_card(client, bot, session_factory):
    request_id = await seed_request(session_factory, CLIENT, "Описание карточки")
    await client.press(action="open", id=request_id)
    edit = [c for c in bot.session.calls if type(c).__name__ == "EditMessageText"][-1]
    assert f"Заявка #{request_id}" in edit.text and "Описание карточки" in edit.text
    labels = [b.text for row in edit.reply_markup.inline_keyboard for b in row]
    assert "Редактировать" in labels


async def test_foreign_request_id_gives_not_found(client, bot, session_factory):
    foreign = await seed_request(session_factory, OTHER, "чужое")
    await client.press(action="open", id=foreign)
    assert NOT_FOUND in bot.session.alerts()
    assert not any("чужое" in t for t in bot.session.texts())


async def test_deleted_request_id_gives_not_found_not_in_work(client, bot, session_factory):
    request_id = await seed_request(session_factory, CLIENT)
    async with session_factory() as session:
        await request_service.delete_request(session, request_id)
    await client.press(action="edit", id=request_id)
    await client.press(action="ef", id=request_id, status="description")
    assert bot.session.alerts() == [NOT_FOUND, NOT_FOUND]
    assert NOT_EDITABLE not in bot.session.texts()


async def test_edit_description_in_new_updates_card(client, bot, session_factory):
    request_id = await seed_request(session_factory, CLIENT, "старое")
    await client.press(action="open", id=request_id)
    await client.press(action="edit", id=request_id)
    await client.press(action="ef", id=request_id, status="description")
    assert await client.state() == EditRequest.value.state

    await client.say("новое описание")

    async with session_factory() as session:
        request = await session.get(Request, request_id)
    assert request.description == "новое описание"
    assert await client.state() is None
    last_card = bot.session.sent(CLIENT)[-1]
    assert "новое описание" in last_card.text and f"Заявка #{request_id}" in last_card.text


async def test_edit_sticker_keeps_value_step(client, bot, session_factory):
    request_id = await seed_request(session_factory, CLIENT, "старое")
    await client.press(action="ef", id=request_id, status="description")
    await client.sticker()
    assert await client.state() == EditRequest.value.state
    assert bot.session.texts()[-1] == "Отправьте ответ текстом"


async def test_edit_invalid_contact_stays_on_step(client, bot, session_factory):
    request_id = await seed_request(session_factory, CLIENT)
    await client.press(action="ef", id=request_id, status="contact")
    await client.say("abc")
    assert await client.state() == EditRequest.value.state
    assert "Неверный контакт" in bot.session.texts()[-1]


async def test_edit_date_by_button(client, bot, session_factory, settings):
    request_id = await seed_request(session_factory, CLIENT)
    await client.press(action="ef", id=request_id, status="preferred_contact_date")
    await client.press(action="date", status="today")
    async with session_factory() as session:
        request = await session.get(Request, request_id)
    assert request.preferred_contact_date == to_local(utcnow(), settings.timezone).date()


async def test_edit_after_admin_took_in_work_is_rejected(client, bot, session_factory):
    request_id = await seed_request(session_factory, CLIENT, "старое")
    await client.press(action="ef", id=request_id, status="description")
    async with session_factory() as session:
        admin = User(tg_id=ADMIN, username=None, full_name="adm", role=Role.admin)
        session.add(admin)
        await session.commit()
        await request_service.change_status(session, request_id, Status.in_progress, admin)

    await client.say("новое описание")

    assert bot.session.texts()[-1] != "новое описание"
    assert any(NOT_EDITABLE in t for t in bot.session.texts(CLIENT))
    async with session_factory() as session:
        request = await session.get(Request, request_id)
    assert request.description == "старое"
    assert await client.state() is None


async def test_in_progress_card_has_no_edit_button(client, bot, session_factory):
    request_id = await seed_request(session_factory, CLIENT, status=Status.in_progress)
    await client.press(action="open", id=request_id)
    edit = [c for c in bot.session.calls if type(c).__name__ == "EditMessageText"][-1]
    labels = [b.text for row in edit.reply_markup.inline_keyboard for b in row]
    assert "Редактировать" not in labels


async def test_admin_can_create_request(dp, bot, session_factory):
    admin = Chatter(dp, bot, ADMIN, "Админ")
    await admin.create_until_confirm()
    await admin.press(action="submit")
    assert len(await all_requests(session_factory)) == 1
    assert bot.session.sent(ADMIN)[-1].text.startswith("Заявка")  # подтверждение автору; себе уведомление не шлётся


async def test_edit_of_request_deleted_meanwhile_gives_not_found(client, bot, session_factory):
    request_id = await seed_request(session_factory, CLIENT, "старое")
    await client.press(action="ef", id=request_id, status="description")
    async with session_factory() as session:
        await request_service.delete_request(session, request_id)

    await client.say("новое описание")

    client_texts = bot.session.texts(CLIENT)
    assert NOT_FOUND in client_texts
    assert not any(NOT_EDITABLE in t for t in client_texts)
    assert await client.state() is None


async def test_submit_service_failure_restores_state_and_retry_creates_one(client, bot, session_factory, monkeypatch):
    from app.bot.routers.client import create_request as module

    real = module.request_service.create_request
    calls = {"n": 0}

    async def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database is locked")
        return await real(*args, **kwargs)

    monkeypatch.setattr(module.request_service, "create_request", flaky)
    await client.create_until_confirm()

    await client.press(action="submit")

    assert bot.session.texts()[-1] == "Не удалось сохранить заявку, попробуйте ещё раз"
    assert await all_requests(session_factory) == []
    assert await client.state() == "CreateRequest:confirm"

    await client.press(action="submit")
    await client.press(action="submit")  # двойной клик после успеха: состояния уже нет

    assert len(await all_requests(session_factory)) == 1
    assert len(bot.session.sent(ADMIN)) == 1


async def test_stale_edit_button_on_request_in_work_alerts_without_entering_flow(client, bot, session_factory):
    request_id = await seed_request(session_factory, CLIENT, status=Status.in_progress)

    await client.press(action="edit", id=request_id)

    assert bot.session.alerts() == [NOT_EDITABLE]
    assert await client.state() is None
    assert not any("Что изменить" in t for t in bot.session.texts())
    card = [c for c in bot.session.calls if type(c).__name__ == "EditMessageText"][-1]
    assert f"Заявка #{request_id}" in card.text
    labels = [b.text for row in card.reply_markup.inline_keyboard for b in row]
    assert "Редактировать" not in labels


async def test_edit_button_on_new_request_still_enters_flow(client, bot, session_factory):
    request_id = await seed_request(session_factory, CLIENT)

    await client.press(action="edit", id=request_id)

    assert await client.state() == "EditRequest:field"
    assert NOT_EDITABLE not in bot.session.alerts()
