from datetime import datetime

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Chat, Message, Update
from aiogram.types import User as TgUser
from sqlalchemy import select

from app.bot.states import CreateRequest
from app.config import Settings
from app.db.models import Role, User
from app.main import build_dispatcher, load_settings_or_exit


class RecordingSession(AiohttpSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        return Message(message_id=1, date=datetime.now(), chat=Chat(id=1, type="private"))


@pytest.fixture
def settings():
    return Settings(bot_token="1:TEST", admin_ids=[100], _env_file=None)


@pytest.fixture
async def bot():
    bot = Bot("1:TEST", session=RecordingSession())
    yield bot
    await bot.session.close()


def make_update(user_id: int, text: str) -> Update:
    message = Message(
        message_id=1,
        date=datetime.now(),
        chat=Chat(id=user_id, type="private"),
        from_user=TgUser(id=user_id, is_bot=False, first_name="Тест"),
        text=text,
    )
    return Update(update_id=1, message=message)


def test_dispatcher_builds(session_factory, settings):
    dp = build_dispatcher(session_factory, settings)
    assert isinstance(dp, Dispatcher)
    assert dp["settings"] is settings
    assert [r.name for r in dp.sub_routers] == [
        "common", "create_request", "my_requests", "edit_request",
        "requests_list", "request_card", "analytics", "export", "fallback",
    ]


def test_dispatcher_includes_client_routers(session_factory, settings):
    names = [r.name for r in build_dispatcher(session_factory, settings).sub_routers]
    assert {"create_request", "my_requests", "edit_request"} <= set(names)
    assert names[0] == "common"  # «Отмена» должна выигрывать в любом состоянии FSM


def test_dispatcher_includes_admin_routers(session_factory, settings):
    names = [r.name for r in build_dispatcher(session_factory, settings).sub_routers]
    assert names[:4] == ["common", "create_request", "my_requests", "edit_request"]
    assert names[4:] == ["requests_list", "request_card", "analytics", "export", "fallback"]


def test_router_factories_return_fresh_routers():
    from app.bot.routers.admin import analytics, export, request_card, requests_list
    from app.bot.routers import fallback
    from app.bot.routers.client import create_request, edit_request, my_requests

    for module in (create_request, my_requests, edit_request, requests_list, request_card, analytics, export, fallback):
        assert module.build_router() is not module.build_router()


async def test_start_creates_user_with_role_and_shows_menu(session_factory, settings, bot):
    dp = build_dispatcher(session_factory, settings)
    await dp.feed_update(bot, make_update(100, "/start"))
    await dp.feed_update(bot, make_update(200, "/start"))

    async with session_factory() as session:
        users = {u.tg_id: u for u in (await session.scalars(select(User))).all()}
    assert users[100].role == Role.admin
    assert users[200].role == Role.client

    sent = bot.session.calls
    assert len(sent) == 2
    admin_menu = [b.text for row in sent[0].reply_markup.keyboard for b in row]
    client_menu = [b.text for row in sent[1].reply_markup.keyboard for b in row]
    assert "Аналитика" in admin_menu
    assert client_menu == ["Новая заявка", "Мои заявки"]


async def test_role_is_recomputed_on_every_update(session_factory, bot):
    first = Settings(bot_token="1:TEST", admin_ids=[100], _env_file=None)
    await build_dispatcher(session_factory, first).feed_update(bot, make_update(200, "/start"))
    second = Settings(bot_token="1:TEST", admin_ids=[100, 200], _env_file=None)
    await build_dispatcher(session_factory, second).feed_update(bot, make_update(200, "/start"))
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.tg_id == 200))
    assert user.role == Role.admin


@pytest.mark.parametrize("cancel", ["Отмена", "/cancel"])
async def test_cancel_clears_fsm_state_in_any_state(session_factory, settings, bot, cancel):
    dp = build_dispatcher(session_factory, settings)
    key = StorageKey(bot_id=bot.id, chat_id=200, user_id=200)
    await dp.storage.set_state(key, CreateRequest.description)

    await dp.feed_update(bot, make_update(200, cancel))

    assert await dp.storage.get_state(key) is None
    assert len(bot.session.calls) == 1


async def test_update_without_from_user_does_not_crash(session_factory, settings, bot):
    dp = build_dispatcher(session_factory, settings)
    message = Message(message_id=1, date=datetime.now(), chat=Chat(id=-5, type="channel"), text="/help")
    await dp.feed_update(bot, Update(update_id=2, message=message))  # не бросает


def test_missing_settings_exit_with_russian_message(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("ADMIN_IDS", raising=False)
    with pytest.raises(SystemExit) as exc:
        load_settings_or_exit()
    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert "BOT_TOKEN" in err and "ADMIN_IDS" in err
    assert "Traceback" not in err


def test_invalid_log_level_exit_names_the_field(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("ADMIN_IDS", "1")
    monkeypatch.setenv("LOG_LEVEL", "VERBOSE")
    with pytest.raises(SystemExit) as exc:
        load_settings_or_exit()
    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert "LOG_LEVEL" in err
    assert "Traceback" not in err
