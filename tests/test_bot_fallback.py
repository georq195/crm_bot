"""Запасные обработчики: устаревшие кнопки, непонятный текст, глобальная ошибка."""
from aiogram.methods import AnswerCallbackQuery
from aiogram.types import CallbackQuery

from app.db.models import Request, Status
from helpers import ADMIN, CLIENT, history_count, seed

STALE = "Кнопка устарела. Откройте меню и повторите действие."


def answers(bot) -> list[AnswerCallbackQuery]:
    return [c for c in bot.session.calls if isinstance(c, AnswerCallbackQuery)]


async def test_stale_date_button_after_flow_ended_is_answered_once(client, bot):
    await client.press(action="date", status="today")

    assert [(a.text, a.show_alert) for a in answers(bot)] == [(STALE, True)]
    assert bot.session.sent() == [] and bot.session.edits() == []


async def test_client_pressing_admin_status_button_gets_alert_and_changes_nothing(client, bot, session_factory):
    request_id = await seed(session_factory)

    await client.press(action="status", id=request_id, status="in_progress")

    assert [(a.text, a.show_alert) for a in answers(bot)] == [(STALE, True)]
    async with session_factory() as session:
        assert (await session.get(Request, request_id)).status == Status.new
    assert await history_count(session_factory, request_id) == 1


async def test_valid_callback_still_reaches_its_handler(admin, bot, session_factory):
    request_id = await seed(session_factory)

    await admin.press(action="open", id=request_id)

    assert STALE not in bot.session.alerts()
    assert bot.session.edits()[-1].text.startswith(f"Заявка #{request_id}")


UNKNOWN = "Не понял сообщение. Выберите действие в меню."


def menu_labels(call) -> list[str]:
    return [b.text for row in call.reply_markup.keyboard for b in row]


async def test_random_text_without_state_gets_reply_with_client_menu(client, bot):
    await client.say("абракадабра")

    assert [c.text for c in bot.session.sent(CLIENT)] == [UNKNOWN]
    assert menu_labels(bot.session.sent(CLIENT)[0]) == ["Новая заявка", "Мои заявки"]


async def test_sticker_without_state_gets_same_reply(client, bot):
    await client.sticker()

    assert [c.text for c in bot.session.sent(CLIENT)] == [UNKNOWN]


async def test_admin_gets_admin_menu_in_fallback(admin, bot):
    await admin.say("абракадабра")

    sent = bot.session.sent(ADMIN)
    assert [c.text for c in sent] == [UNKNOWN]
    assert "Аналитика" in menu_labels(sent[0])


async def test_fallback_does_not_fire_inside_fsm_step(client, bot, session_factory):
    await client.say("Новая заявка")
    await client.say("Иван")
    await client.say("+7 999 123-45-67")

    await client.say("мое описание")

    assert UNKNOWN not in [c.text for c in bot.session.sent(CLIENT)]
    assert await client.state() == "CreateRequest:date"


ERROR_TEXT = "Что-то пошло не так. Попробуйте ещё раз."


async def test_error_in_callback_handler_alerts_user_and_is_logged(admin, bot, session_factory, monkeypatch, caplog):
    from app.services import request_service

    async def boom(*args, **kwargs):
        raise RuntimeError("db is gone")

    request_id = await seed(session_factory)
    monkeypatch.setattr(request_service, "change_status", boom)

    await admin.press(action="status", id=request_id, status="in_progress")  # не должно бросить

    assert [(a.text, a.show_alert) for a in answers(bot)] == [(ERROR_TEXT, True)]
    assert any(r.exc_info and "db is gone" in str(r.exc_info[1]) for r in caplog.records)


async def test_error_in_message_handler_replies_to_user_and_is_logged(client, bot, monkeypatch, caplog):
    from app.services import request_service

    async def boom(*args, **kwargs):
        raise RuntimeError("db is gone")

    monkeypatch.setattr(request_service, "list_requests", boom)

    await client.say("Мои заявки")

    assert [c.text for c in bot.session.sent(CLIENT)] == [ERROR_TEXT]
    assert any(r.exc_info and "db is gone" in str(r.exc_info[1]) for r in caplog.records)


async def test_failure_of_error_reply_is_swallowed_and_logged(admin, bot, session_factory, monkeypatch, caplog):
    from app.services import request_service

    async def boom(*args, **kwargs):
        raise RuntimeError("db is gone")

    async def broken_answer(self, *args, **kwargs):
        raise RuntimeError("telegram is down")

    request_id = await seed(session_factory)
    monkeypatch.setattr(request_service, "change_status", boom)
    monkeypatch.setattr(CallbackQuery, "answer", broken_answer)

    await admin.press(action="status", id=request_id, status="in_progress")  # не должно бросить

    assert any(r.exc_info and "telegram is down" in str(r.exc_info[1]) for r in caplog.records)
