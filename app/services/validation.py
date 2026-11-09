import re
from datetime import date, datetime

NOT_TEXT = "Отправьте ответ текстом"

_PHONE_RE = re.compile(r"\+?[0-9]{10,15}")
_USERNAME_RE = re.compile(r"@[A-Za-z0-9_]{5,32}")


class InvalidInput(ValueError):
    """Ошибка ввода; сообщение по-русски, показывается пользователю."""


def _text(text: str | None) -> str:
    if text is None:
        raise InvalidInput(NOT_TEXT)
    return text.strip()


def _bounded(text: str | None, label: str, max_len: int) -> str:
    value = _text(text)
    if not value:
        raise InvalidInput(f"{label} не может быть пустым")
    if len(value) > max_len:
        raise InvalidInput(f"{label} слишком длинное: максимум {max_len} символов")
    return value


def validate_name(text: str | None) -> str:
    return _bounded(text, "Имя", 100)


def validate_description(text: str | None) -> str:
    return _bounded(text, "Описание", 1000)


def validate_comment(text: str | None) -> str:
    return _bounded(text, "Комментарий", 300)


def validate_search_query(text: str | None) -> str:
    return _bounded(text, "Запрос", 100)


def validate_contact(text: str | None) -> str:
    value = _text(text)
    if _USERNAME_RE.fullmatch(value):
        return value
    phone = re.sub(r"[\s()\-]", "", value)
    if _PHONE_RE.fullmatch(phone):
        return phone
    raise InvalidInput(
        "Неверный контакт. Укажите телефон (например, +7 999 123-45-67) "
        "или username в формате @username"
    )


def parse_preferred_date(text: str | None, today: date) -> date:
    value = _text(text)
    try:
        parsed = datetime.strptime(value, "%d.%m.%Y").date()
    except ValueError:
        raise InvalidInput("Неверная дата. Укажите дату в формате ДД.ММ.ГГГГ") from None
    if parsed < today:
        raise InvalidInput("Дата не может быть в прошлом")
    return parsed
