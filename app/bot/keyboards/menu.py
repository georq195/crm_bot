from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from app.bot.texts import BTN_ANALYTICS, BTN_EXPORT, BTN_MY_REQUESTS, BTN_NEW_REQUEST, BTN_REQUESTS, BTN_SEARCH
from app.db.models import Role


def main_menu(role: Role) -> ReplyKeyboardMarkup:
    if role == Role.admin:
        rows = [
            [BTN_REQUESTS, BTN_SEARCH],
            [BTN_ANALYTICS, BTN_EXPORT],
            [BTN_NEW_REQUEST],
        ]
    else:
        rows = [[BTN_NEW_REQUEST, BTN_MY_REQUESTS]]
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t) for t in row] for row in rows], resize_keyboard=True
    )
