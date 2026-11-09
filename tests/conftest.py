import pytest
import pytest_asyncio
from aiogram import Bot

from app.config import Settings
from app.db.base import init_db, make_engine, make_session_factory
from app.main import build_dispatcher
from helpers import ADMIN, CLIENT, Chatter, FakeSession


@pytest_asyncio.fixture
async def engine():
    engine = make_engine(":memory:")
    await init_db(engine)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    return make_session_factory(engine)


@pytest_asyncio.fixture
async def session(session_factory):
    async with session_factory() as session:
        yield session


class FakeSender:
    """Записывает вызовы send как (tg_id, text, open_request_id)."""

    def __init__(self):
        self.calls: list[tuple[int, str, int | None]] = []

    async def send(self, tg_id: int, text: str, open_request_id: int | None = None) -> bool:
        self.calls.append((tg_id, text, open_request_id))
        return True


class FailingSender:
    async def send(self, tg_id: int, text: str, open_request_id: int | None = None) -> bool:
        raise RuntimeError("telegram is down")


@pytest.fixture
def sender():
    return FakeSender()


@pytest.fixture
def failing_sender():
    return FailingSender()


@pytest.fixture
def settings():
    return Settings(bot_token="1:TEST", admin_ids=[ADMIN], _env_file=None)


@pytest.fixture
async def bot():
    bot = Bot("1:TEST", session=FakeSession())
    yield bot
    await bot.session.close()


@pytest.fixture
def dp(session_factory, settings):
    return build_dispatcher(session_factory, settings)


@pytest.fixture
def client(dp, bot):
    return Chatter(dp, bot, CLIENT)


@pytest.fixture
def admin(dp, bot):
    return Chatter(dp, bot, ADMIN, "Админ")
