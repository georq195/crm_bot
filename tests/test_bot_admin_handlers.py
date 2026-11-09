"""Сценарные тесты админских роутеров: настоящие Update через dp.feed_update, Bot с записывающей сессией."""
from pathlib import Path

import pytest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from sqlalchemy import func, select

from app.bot.callbacks import RequestCB
from app.bot.states import AdminInput
from app.db.models import Comment, Request, Role, Status, StatusHistory
from app.services import request_service
from helpers import ADMIN, CLIENT, NOT_FOUND, get_or_create_user, all_requests, history_count, seed


def buttons(call) -> list:
    return [b for row in call.reply_markup.inline_keyboard for b in row]


def request_buttons(call) -> list[RequestCB]:
    unpacked = [RequestCB.unpack(b.callback_data) for b in buttons(call) if b.callback_data.startswith("req:")]
    return [cb for cb in unpacked if cb.action == "open"]


def last_with_markup(bot):
    return [c for c in bot.session.calls if isinstance(c, (SendMessage, EditMessageText)) and c.reply_markup][-1]


async def test_admin_opens_list_and_filters_new(admin, bot, session_factory):
    first = await seed(session_factory, "Иван Петров")
    second = await seed(session_factory, "Мария Сидорова")
    taken = await seed(session_factory, "Пётр Иванов", status=Status.in_progress)

    await admin.say("Заявки")
    listing = last_with_markup(bot)
    assert listing.text == "Заявки: Все"
    assert {cb.id for cb in request_buttons(listing)} == {first, second, taken}

    await admin.press(action="filter", status="new")

    filtered = bot.session.edits()[-1]
    assert filtered.text == "Заявки: Новые"
    assert {cb.id for cb in request_buttons(filtered)} == {first, second}
    assert any(b.text == "• Новые" for b in buttons(filtered))


async def test_empty_filter_shows_message_and_filter_buttons(admin, bot, session_factory):
    await seed(session_factory)
    await admin.say("Заявки")
    await admin.press(action="filter", status="rejected")
    edited = bot.session.edits()[-1]
    assert edited.text == "Заявок нет: Отклонённые"
    assert request_buttons(edited) == []


async def test_search_finds_request_by_part_of_name_in_other_case(admin, bot, session_factory):
    wanted = await seed(session_factory, "Иван Петров")
    await seed(session_factory, "Мария Сидорова")

    await admin.say("Поиск")
    assert await admin.state() == AdminInput.search.state
    await admin.say("ПЕТР")

    assert await admin.state() is None
    listing = last_with_markup(bot)
    assert listing.text == "Результаты поиска: ПЕТР"
    assert [cb.id for cb in request_buttons(listing)] == [wanted]


async def test_search_paging_reads_query_from_fsm_data(admin, bot, session_factory):
    ids = [await seed(session_factory, f"Петров {i}") for i in range(6)]
    await seed(session_factory, "Сидоров")
    await admin.say("Поиск")
    await admin.say("петров")
    assert (await admin.dp.storage.get_data(admin.key))["query"] == "петров"

    await admin.press(action="list", page=1)

    edited = bot.session.edits()[-1]
    assert edited.text == "Результаты поиска: петров"
    assert [cb.id for cb in request_buttons(edited)] == [ids[0]]  # 6 совпадений: на второй странице самая старая


async def test_search_without_results(admin, bot, session_factory):
    await seed(session_factory)
    await admin.say("Поиск")
    await admin.say("несуществующий")
    assert bot.session.texts()[-1] == "Ничего не найдено по запросу: несуществующий"


@pytest.mark.parametrize("kind", ["sticker", "photo"])
async def test_search_step_keeps_state_and_asks_for_text(admin, bot, kind):
    await admin.say("Поиск")
    await getattr(admin, kind)()
    assert await admin.state() == AdminInput.search.state
    assert bot.session.texts()[-1] == "Отправьте ответ текстом"


async def test_status_change_notifies_client_and_updates_card(admin, bot, session_factory):
    request_id = await seed(session_factory)

    await admin.press(action="status", id=request_id, status="in_progress")

    async with session_factory() as session:
        request = await session.get(Request, request_id)
        assert request.status == Status.in_progress
    notices = bot.session.sent(CLIENT)
    assert len(notices) == 1
    assert notices[0].text == f"Статус вашей заявки #{request_id} изменён: В работе"
    card = bot.session.edits()[-1]
    assert card.text.startswith(f"Заявка #{request_id}: В работе")
    assert {b.text for b in buttons(card)} >= {"→ Завершена", "→ Отклонена"}
    assert "→ В работе" not in {b.text for b in buttons(card)}
    assert await history_count(session_factory, request_id) == 2


async def test_card_status_buttons_follow_transitions(admin, bot, session_factory):
    request_id = await seed(session_factory)
    await admin.press(action="open", id=request_id)
    labels = {b.text for b in buttons(bot.session.edits()[-1])}
    assert {"→ В работе", "→ Отклонена", "Комментарий", "Удалить", "К списку"} <= labels
    assert "→ Завершена" not in labels

    done = await seed(session_factory, status=Status.rejected)
    await admin.press(action="open", id=done)
    labels = {b.text for b in buttons(bot.session.edits()[-1])}
    assert not any(label.startswith("→") for label in labels)


async def test_repeated_status_button_from_old_card_shows_error_without_second_history_row(
    admin, bot, session_factory
):
    request_id = await seed(session_factory)
    await admin.press(action="status", id=request_id, status="in_progress")
    assert await history_count(session_factory, request_id) == 2
    alerts_before = len(bot.session.alerts())

    await admin.press(action="status", id=request_id, status="in_progress")  # та же кнопка старой карточки

    assert bot.session.alerts()[alerts_before:] == ["Статус уже изменён, карточка обновлена"]
    assert await history_count(session_factory, request_id) == 2
    assert len(bot.session.sent(CLIENT)) == 1  # повторного уведомления нет
    assert bot.session.edits()[-1].text.startswith(f"Заявка #{request_id}: В работе")
    async with session_factory() as session:
        assert (await session.get(Request, request_id)).status == Status.in_progress


async def test_invalid_transition_from_other_status_is_rejected(admin, bot, session_factory):
    request_id = await seed(session_factory, status=Status.done)
    await admin.press(action="status", id=request_id, status="new")
    assert bot.session.alerts()[-1] == "Статус уже изменён, карточка обновлена"
    assert await history_count(session_factory, request_id) == 3


async def test_comment_is_added_and_shown_on_card(admin, bot, session_factory):
    request_id = await seed(session_factory)

    await admin.press(action="comment", id=request_id)
    assert await admin.state() == AdminInput.comment.state
    await admin.say("Перезвонить завтра")

    assert await admin.state() is None
    async with session_factory() as session:
        comments = (await session.scalars(select(Comment))).all()
    assert [c.text for c in comments] == ["Перезвонить завтра"]
    assert bot.session.texts()[-2] == "Комментарий добавлен."
    assert "Перезвонить завтра" in bot.session.texts()[-1]


@pytest.mark.parametrize("kind", ["sticker", "photo"])
async def test_comment_step_keeps_state_and_asks_for_text(admin, bot, session_factory, kind):
    request_id = await seed(session_factory)
    await admin.press(action="comment", id=request_id)

    await getattr(admin, kind)()

    assert await admin.state() == AdminInput.comment.state
    assert bot.session.texts()[-1] == "Отправьте ответ текстом"
    async with session_factory() as session:
        assert (await session.scalar(select(func.count()).select_from(Comment))) == 0


async def test_too_long_comment_explains_and_stays(admin, bot, session_factory):
    request_id = await seed(session_factory)
    await admin.press(action="comment", id=request_id)
    await admin.say("я" * 301)
    assert await admin.state() == AdminInput.comment.state
    assert "слишком длинное" in bot.session.texts()[-1]


async def test_comment_on_request_deleted_meanwhile(admin, bot, session_factory):
    request_id = await seed(session_factory)
    await admin.press(action="comment", id=request_id)
    async with session_factory() as session:
        await request_service.delete_request(session, request_id)

    await admin.say("Поздно")

    assert await admin.state() is None
    assert NOT_FOUND in bot.session.texts()


async def test_client_cannot_use_admin_callbacks(client, bot, session_factory):
    request_id = await seed(session_factory)
    for cb in (
        RequestCB(action="status", id=request_id, status="in_progress"),
        RequestCB(action="delete_ok", id=request_id),
        RequestCB(action="comment", id=request_id),
        RequestCB(action="filter", status="new"),
    ):
        await client.press_cb(cb.pack())
    await client.period("stats", "all")
    await client.export("csv", "all", "all")
    await client.say("Заявки")
    await client.say("Аналитика")

    # Админские кнопки меню клиенту недоступны: он получает только общий ответ «не понял».
    assert [c.text for c in bot.session.sent()] == ["Не понял сообщение. Выберите действие в меню."] * 2
    assert bot.session.edits() == []
    assert set(bot.session.alerts()) == {"Кнопка устарела. Откройте меню и повторите действие."}
    assert bot.session.documents() == []
    async with session_factory() as session:
        request = await session.get(Request, request_id)
        assert request.status == Status.new
    assert await client.state() is None


async def test_admin_card_actions_are_not_swallowed_by_client_fallback(admin, bot, session_factory):
    request_id = await seed(session_factory)
    await admin.press(action="open", id=request_id)
    assert bot.session.edits()[-1].text.startswith(f"Заявка #{request_id}")
    assert "Заявка уже отправлена или отменена" not in bot.session.alerts()


async def test_delete_requires_confirmation_then_old_card_is_not_found(admin, bot, session_factory):
    request_id = await seed(session_factory)
    await admin.press(action="open", id=request_id)

    await admin.press(action="delete", id=request_id)
    assert "Удалить заявку" in bot.session.edits()[-1].text
    assert len(await all_requests(session_factory)) == 1  # без подтверждения заявка на месте

    await admin.press(action="delete_ok", id=request_id)
    assert await all_requests(session_factory) == []
    assert bot.session.alerts()[-1] == f"Заявка #{request_id} удалена"

    for action, extra in (("status", {"status": "done"}), ("comment", {}), ("open", {}), ("delete_ok", {})):
        await admin.press(action=action, id=request_id, **extra)
        assert bot.session.alerts()[-1] == NOT_FOUND
        assert bot.session.edits()[-1].text.startswith("Заявок нет")
    assert await admin.state() is None


async def test_delete_removes_comments_and_history(admin, bot, session_factory):
    request_id = await seed(session_factory)
    await admin.press(action="comment", id=request_id)
    await admin.say("Заметка")
    await admin.press(action="delete_ok", id=request_id)
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Comment)) == 0
        assert await session.scalar(select(func.count()).select_from(StatusHistory)) == 0


async def test_open_stale_id_returns_to_list(admin, bot, session_factory):
    other = await seed(session_factory)
    await admin.press(action="open", id=9999)
    assert bot.session.alerts()[-1] == NOT_FOUND
    assert [cb.id for cb in request_buttons(bot.session.edits()[-1])] == [other]


async def test_analytics_shows_stats_for_period(admin, bot, session_factory):
    await seed(session_factory)
    await seed(session_factory, status=Status.done)

    await admin.say("Аналитика")
    assert [b.text for b in buttons(last_with_markup(bot))] == ["Сегодня", "7 дней", "30 дней", "Всё время"]
    await admin.period("stats", "all")

    text = bot.session.edits()[-1].text
    assert text.startswith("Аналитика: Всё время")
    assert "Всего заявок: 2" in text and "Завершено: 1" in text


@pytest.mark.parametrize("fmt", ["csv", "xlsx"])
async def test_export_sends_document_and_deletes_file(admin, bot, session_factory, monkeypatch, tmp_path, fmt):
    monkeypatch.chdir(tmp_path)
    await seed(session_factory, "Иван Петров")

    await admin.say("Экспорт")
    assert {b.text for b in buttons(last_with_markup(bot))} == {"CSV", "XLSX"}
    await admin.export(fmt, "", "")
    assert bot.session.edits()[-1].text == "Выберите период выгрузки (по дате создания заявок)."
    await admin.period(fmt, "all")
    assert bot.session.edits()[-1].text == "Выберите статус заявок для выгрузки."
    assert bot.session.documents() == []
    await admin.export(fmt, "all", "all")

    [document] = bot.session.documents()
    assert document.chat_id == ADMIN
    path = Path(document.document.path)
    assert path.suffix == f".{fmt}"
    assert bot.session.document_existed == [True]  # файл был на месте в момент отправки
    assert not path.exists()
    assert list((tmp_path / "exports").iterdir()) == []


async def test_export_file_deleted_when_sending_fails(admin, bot, session_factory, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    await seed(session_factory)
    bot.session.fail_documents = True

    await admin.export("csv", "all", "all")

    assert bot.session.texts()[-1] == "Не удалось отправить файл, попробуйте ещё раз"
    assert list((tmp_path / "exports").iterdir()) == []


async def test_export_filters_by_status(admin, bot, session_factory, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    await seed(session_factory)
    await admin.export("csv", "all", "done")
    assert bot.session.documents() == []
    assert bot.session.texts()[-1] == "Заявок по выбранным фильтрам нет"


async def test_export_without_requests_answers_and_sends_no_document(admin, bot, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    await admin.export("xlsx", "today", "all")

    assert bot.session.texts()[-1] == "Заявок по выбранным фильтрам нет"
    assert bot.session.documents() == []
    assert not (tmp_path / "exports").exists() or list((tmp_path / "exports").iterdir()) == []


async def test_stale_export_buttons_do_not_crash(admin, bot):
    await admin.export("pdf", "", "")
    await admin.export("csv", "forever", "all")
    await admin.export("csv", "all", "bogus")
    await admin.period("csv", "forever")
    await admin.period("stats", "forever")
    assert bot.session.documents() == []
    assert bot.session.alerts() == ["Кнопка устарела, начните заново"] * 5


MENU_REPLIES = {
    "Заявки": "Заявки: Все",
    "Поиск": "Введите часть имени или контакта.",
    "Аналитика": "Аналитика: выберите период.",
    "Экспорт": "Выберите формат выгрузки.",
    "Новая заявка": "Как вас зовут? Выберите имя кнопкой или введите своё.",
}


@pytest.mark.parametrize("button", list(MENU_REPLIES))
async def test_menu_button_leaves_comment_step(admin, bot, session_factory, button):
    request_id = await seed(session_factory)
    await admin.press(action="comment", id=request_id)
    assert await admin.state() == AdminInput.comment.state

    await admin.say(button)

    assert await admin.state() != AdminInput.comment.state
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Comment)) == 0
    assert bot.session.texts()[-1] == MENU_REPLIES[button]
    assert "Комментарий добавлен." not in bot.session.texts()


@pytest.mark.parametrize("button", list(MENU_REPLIES))
async def test_menu_button_leaves_search_step(admin, bot, session_factory, button):
    await seed(session_factory)
    await admin.say("Поиск")
    assert await admin.state() == AdminInput.search.state

    await admin.say(button)

    assert await admin.state() != AdminInput.search.state or button == "Поиск"
    assert bot.session.texts()[-1] == MENU_REPLIES[button]
    assert not any(t.startswith(("Результаты поиска", "Ничего не найдено")) for t in bot.session.texts())
    assert "query" not in await admin.dp.storage.get_data(admin.key) or not (
        await admin.dp.storage.get_data(admin.key)
    )["query"]


async def test_export_build_failure_answers_error_and_does_not_crash(admin, bot, session_factory, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    await seed(session_factory)

    async def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.bot.routers.admin.export.export_service.build_export", broken)

    await admin.export("xlsx", "all", "all")

    assert bot.session.texts()[-1].startswith("Не удалось сформировать файл")
    assert bot.session.documents() == []


async def test_admin_card_with_emoji_comments_fits_telegram_limit(admin, bot, session_factory):
    request_id = await seed(session_factory)
    async with session_factory() as session:
        adm = await get_or_create_user(session, ADMIN, Role.admin)
        for _ in range(10):
            await request_service.add_comment(session, request_id, adm, "😀" * 200)

    await admin.press(action="open", id=request_id)

    card = [t for t in bot.session.texts(ADMIN) if t.startswith(f"Заявка #{request_id}")][-1]
    assert len(card.encode("utf-16-le")) // 2 <= 4096


async def test_search_results_do_not_mark_any_filter_active(admin, bot, session_factory):
    await seed(session_factory, "Иван Петров")
    await admin.say("Поиск")
    await admin.say("петров")

    assert not any(b.text.startswith("•") for b in buttons(last_with_markup(bot)))


async def test_search_without_results_does_not_mark_any_filter_active(admin, bot, session_factory):
    await seed(session_factory)
    await admin.say("Поиск")
    await admin.say("несуществующий")

    assert not any(b.text.startswith("•") for b in buttons(last_with_markup(bot)))


def answers_per_callback(bot) -> dict[str, int]:
    counts: dict[str, int] = {}
    for c in bot.session.calls:
        if isinstance(c, AnswerCallbackQuery):
            counts[c.callback_query_id] = counts.get(c.callback_query_id, 0) + 1
    return counts


async def test_each_callback_is_answered_exactly_once_on_stale_paths(admin, bot, session_factory):
    request_id = await seed(session_factory)
    await admin.press(action="status", id=request_id, status="in_progress")
    await admin.press(action="status", id=request_id, status="in_progress")  # InvalidTransition
    await admin.press(action="delete_ok", id=request_id)
    for action, extra in (("status", {"status": "done"}), ("delete_ok", {}), ("delete", {}), ("open", {})):
        await admin.press(action=action, id=request_id, **extra)  # RequestNotFound / stale

    counts = answers_per_callback(bot)
    assert len(counts) == 7
    assert set(counts.values()) == {1}


async def test_invalid_transition_on_request_deleted_meanwhile_answers_once(admin, bot, session_factory, monkeypatch):
    request_id = await seed(session_factory)

    async def lose_race(session, rid, new_status, user, **kw):
        await request_service.delete_request(session, rid)
        raise request_service.InvalidTransition("race")

    monkeypatch.setattr(request_service, "change_status", lose_race)
    await admin.press(action="status", id=request_id, status="in_progress")

    assert list(answers_per_callback(bot).values()) == [1]
    assert bot.session.alerts() == [NOT_FOUND]
