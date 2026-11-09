"""Общие помощники для тестов бота: запись вызовов API, «собеседник» и заготовки данных."""
from datetime import date, datetime, timedelta
from pathlib import Path

from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.storage.base import StorageKey
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendDocument, SendMessage, TelegramMethod
from aiogram.types import CallbackQuery, Chat, Message, PhotoSize, Sticker, Update
from aiogram.types import User as TgUser
from sqlalchemy import func, select

from app.bot.callbacks import ExportCB, PeriodCB, RequestCB
from app.db.models import Request, Role, Status, StatusHistory, User
from app.services import request_service

ADMIN = 100
CLIENT = 200
OTHER = 300
NOT_FOUND = "Заявка не найдена"
NOT_EDITABLE = "Заявку уже взяли в работу, редактирование недоступно"


class FakeSession(BaseSession):
    """Не ходит в сеть: записывает вызовы API. Помнит, существовал ли файл документа в момент отправки."""

    def __init__(self):
        super().__init__()
        self.calls: list[TelegramMethod] = []
        self.fail_documents = False
        self.document_existed: list[bool] = []

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, SendDocument):
            self.document_existed.append(Path(method.document.path).exists())
            if self.fail_documents:
                raise TelegramBadRequest(method=method, message="boom")
        self.calls.append(method)
        if method.__returning__ is bool:
            return True
        return Message(message_id=len(self.calls), date=datetime.now(), chat=Chat(id=1, type="private"))

    async def stream_content(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError

    async def close(self):
        pass

    def sent(self, chat_id: int | None = None) -> list[SendMessage]:
        return [
            c for c in self.calls
            if isinstance(c, SendMessage) and (chat_id is None or c.chat_id == chat_id)
        ]

    def texts(self, chat_id: int | None = None) -> list[str]:
        """Тексты всех отправленных и отредактированных сообщений."""
        return [
            c.text for c in self.calls
            if getattr(c, "text", None) is not None and (chat_id is None or getattr(c, "chat_id", chat_id) == chat_id)
        ]

    def alerts(self) -> list[str]:
        return [c.text for c in self.calls if isinstance(c, AnswerCallbackQuery) and c.text]

    def documents(self) -> list[SendDocument]:
        return [c for c in self.calls if isinstance(c, SendDocument)]

    def edits(self) -> list[EditMessageText]:
        return [c for c in self.calls if isinstance(c, EditMessageText)]


class Chatter:
    """Один пользователь: шлёт сообщения и нажимает inline-кнопки."""

    def __init__(self, dp, bot, tg_id: int, name: str = "Иван Петров"):
        self.dp, self.bot, self.tg_id, self.name = dp, bot, tg_id, name
        self._uid = 0

    def _next(self) -> int:
        self._uid += 1
        return self._uid + self.tg_id * 1000

    @property
    def tg_user(self) -> TgUser:
        return TgUser(id=self.tg_id, is_bot=False, first_name=self.name)

    def _message(self, **kwargs) -> Message:
        return Message(
            message_id=self._next(), date=datetime.now(),
            chat=Chat(id=self.tg_id, type="private"), from_user=self.tg_user, **kwargs,
        )

    async def say(self, text: str) -> None:
        await self.dp.feed_update(self.bot, Update(update_id=self._next(), message=self._message(text=text)))

    async def sticker(self) -> None:
        sticker = Sticker(
            file_id="f", file_unique_id="u", type="regular", width=1, height=1,
            is_animated=False, is_video=False,
        )
        await self.dp.feed_update(self.bot, Update(update_id=self._next(), message=self._message(sticker=sticker)))

    async def press(self, **cb_fields) -> None:
        data = RequestCB(**cb_fields).pack()
        query = CallbackQuery(
            id=str(self._next()), from_user=self.tg_user, chat_instance="ci", data=data,
            message=self._message(text="карточка"),
        )
        await self.dp.feed_update(self.bot, Update(update_id=self._next(), callback_query=query))

    @property
    def key(self) -> StorageKey:
        return StorageKey(bot_id=self.bot.id, chat_id=self.tg_id, user_id=self.tg_id)

    async def state(self):
        return await self.dp.storage.get_state(self.key)

    async def create_until_confirm(self, description: str = "Нужна консультация") -> None:
        await self.say("Новая заявка")
        await self.say(self.name)
        await self.say("+7 999 123-45-67")
        await self.say(description)
        await self.press(action="date", status="today")
    async def press_cb(self, packed: str) -> None:
        query = CallbackQuery(
            id=str(self._next()), from_user=self.tg_user, chat_instance="ci", data=packed,
            message=self._message(text="карточка"),
        )
        await self.dp.feed_update(self.bot, Update(update_id=self._next(), callback_query=query))

    async def period(self, kind: str, period: str) -> None:
        await self.press_cb(PeriodCB(kind=kind, period=period).pack())

    async def export(self, fmt: str, period: str, status: str) -> None:
        await self.press_cb(ExportCB(fmt=fmt, period=period, status=status).pack())

    async def photo(self) -> None:
        photo = [PhotoSize(file_id="f", file_unique_id="u", width=1, height=1)]
        await self.dp.feed_update(self.bot, Update(update_id=self._next(), message=self._message(photo=photo)))


async def all_requests(session_factory) -> list[Request]:
    async with session_factory() as session:
        return list((await session.scalars(select(Request).order_by(Request.id))).all())


async def seed_request(session_factory, tg_id: int, description: str = "Описание", status: Status = Status.new) -> int:
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if user is None:
            user = User(tg_id=tg_id, username=None, full_name=f"user{tg_id}", role=Role.client)
            session.add(user)
            await session.commit()
        request = await request_service.create_request(
            session, user, f"Клиент {tg_id}", "+79991234567", description, date.today() + timedelta(days=1)
        )
        request_id = request.id
        if status != Status.new:
            admin = User(tg_id=ADMIN, username=None, full_name="adm", role=Role.admin)
            session.add(admin)
            await session.commit()
            await request_service.change_status(session, request_id, status, admin)
    return request_id


async def get_or_create_user(session, tg_id: int, role: Role) -> User:
    user = await session.scalar(select(User).where(User.tg_id == tg_id))
    if user is None:
        user = User(tg_id=tg_id, username=None, full_name=f"user{tg_id}", role=role)
        session.add(user)
        await session.commit()
    return user


async def seed(session_factory, name: str = "Иван Петров", tg_id: int = CLIENT, status: Status = Status.new) -> int:
    async with session_factory() as session:
        author = await get_or_create_user(session, tg_id, Role.client)
        request = await request_service.create_request(
            session, author, name, "+79991234567", "Нужна консультация", date.today() + timedelta(days=1)
        )
        request_id = request.id
        if status != Status.new:
            admin = await get_or_create_user(session, ADMIN, Role.admin)
            if status == Status.done:
                await request_service.change_status(session, request_id, Status.in_progress, admin)
            await request_service.change_status(session, request_id, status, admin)
    return request_id


async def history_count(session_factory, request_id: int) -> int:
    async with session_factory() as session:
        return await session.scalar(
            select(func.count()).select_from(StatusHistory).where(StatusHistory.request_id == request_id)
        )
