from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot.keyboards.menu import main_menu
from app.bot.texts import (
    BTN_CANCEL,
    CANCELLED,
    HELP_ADMIN,
    HELP_CLIENT,
    WELCOME_ADMIN,
    WELCOME_CLIENT,
)
from app.db.models import Role, User


def _role(user: User | None) -> Role:
    return user.role if user is not None else Role.client


async def cancel(message: Message, state: FSMContext, user: User | None = None) -> None:
    await state.clear()
    await message.answer(CANCELLED, reply_markup=main_menu(_role(user)))


async def start(message: Message, state: FSMContext, user: User | None = None) -> None:
    await state.clear()
    role = _role(user)
    welcome = WELCOME_ADMIN if role == Role.admin else WELCOME_CLIENT
    await message.answer(welcome, reply_markup=main_menu(role))


async def help_command(message: Message, user: User | None = None) -> None:
    role = _role(user)
    await message.answer(HELP_ADMIN if role == Role.admin else HELP_CLIENT, reply_markup=main_menu(role))


def build_router() -> Router:
    router = Router(name="common")
    router.message.register(cancel, Command("cancel"))
    router.message.register(cancel, F.text == BTN_CANCEL)
    router.message.register(start, CommandStart())
    router.message.register(help_command, Command("help"))
    return router
