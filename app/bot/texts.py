from datetime import timedelta

from app.db.models import Comment, Request
from app.labels import STATUS_LABELS
from app.services.analytics_service import Stats
from app.timeutil import to_local

MAX_MESSAGE_LEN = 4096
MAX_CARD_COMMENTS = 10
COMMENT_LIMIT = 200
PAGE_SIZE = 5
_DT_FORMAT = "%d.%m.%Y %H:%M"

BTN_NEW_REQUEST = "Новая заявка"
BTN_MY_REQUESTS = "Мои заявки"
BTN_REQUESTS = "Заявки"
BTN_SEARCH = "Поиск"
BTN_ANALYTICS = "Аналитика"
BTN_EXPORT = "Экспорт"
BTN_CANCEL = "Отмена"
ADMIN_MENU_BUTTONS = frozenset({BTN_REQUESTS, BTN_SEARCH, BTN_ANALYTICS, BTN_EXPORT, BTN_NEW_REQUEST})

WELCOME_CLIENT = "Здравствуйте! Здесь можно оставить заявку и следить за её статусом."
WELCOME_ADMIN = "Здравствуйте! Вы вошли как администратор: заявки, поиск, аналитика и экспорт доступны в меню."
HELP_CLIENT = (
    "Что умеет бот:\n"
    "Новая заявка: оставить заявку\n"
    "Мои заявки: посмотреть свои заявки и их статус\n"
    "/cancel или «Отмена»: прервать текущее действие"
)
HELP_ADMIN = (
    "Что умеет бот:\n"
    "Заявки: список заявок, смена статуса, комментарии\n"
    "Поиск: найти заявку по тексту\n"
    "Аналитика: статистика за период\n"
    "Экспорт: выгрузка заявок в CSV или XLSX\n"
    "Новая заявка: создать заявку\n"
    "/cancel или «Отмена»: прервать текущее действие"
)
CANCELLED = "Действие отменено."


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def fit_message(text: str) -> str:
    """Не длиннее MAX_MESSAGE_LEN единиц UTF-16 (так считает Telegram; эмодзи = 2 единицы).

    Короткий текст возвращается как есть; длинный обрезается с «…» по границе символа.
    """
    if _utf16_len(text) <= MAX_MESSAGE_LEN:
        return text
    budget = MAX_MESSAGE_LEN - 1  # место под «…»
    used = 0
    end = 0
    for char in text:
        units = 2 if ord(char) > 0xFFFF else 1
        if used + units > budget:
            break
        used += units
        end += 1
    return text[:end] + "…"


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)] + "…"


def format_request_card(
    request: Request, comments: list[Comment], *, for_admin: bool, tz: str
) -> str:
    """Карточка заявки обычным текстом (без parse_mode), всегда не длиннее 4096 символов.

    Бюджет: показываются последние 10 комментариев (только админу), каждый
    обрезается с «…»; если всё равно не влезает, обрезается описание.
    """
    head = [
        f"Заявка #{request.id}: {STATUS_LABELS[request.status]}",
        f"Клиент: {request.client_name}",
        f"Контакт: {request.contact}",
        f"Связаться: {request.preferred_contact_date.strftime('%d.%m.%Y')}",
        f"Создана: {to_local(request.created_at, tz).strftime(_DT_FORMAT)}",
        f"Обновлена: {to_local(request.updated_at, tz).strftime(_DT_FORMAT)}",
    ]

    comment_lines: list[str] = []
    if for_admin and comments:
        shown = comments[-MAX_CARD_COMMENTS:]
        title = f"Комментарии ({len(comments)})"
        if len(shown) < len(comments):
            title = f"Комментарии (последние {len(shown)} из {len(comments)})"
        comment_lines.append(title)
        for c in shown:
            stamp = to_local(c.created_at, tz).strftime(_DT_FORMAT)
            body = _shorten(" ".join(c.text.split()), COMMENT_LIMIT)
            comment_lines.append(f"{stamp}: {body}")

    def build(description: str) -> str:
        parts = ["\n".join(head), f"Описание:\n{description}"]
        if comment_lines:
            parts.append("\n".join(comment_lines))
        return "\n\n".join(parts)

    description = request.description
    text = build(description)
    excess = len(text) - MAX_MESSAGE_LEN
    if excess > 0:
        description = _shorten(description, len(description) - excess)
        text = build(description)
    return fit_message(text)  # на случай эмодзи: лимит Telegram считается в единицах UTF-16


def _format_duration(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    days, rest = divmod(minutes, 24 * 60)
    hours, mins = divmod(rest, 60)
    parts = []
    if days:
        parts.append(f"{days} д")
    if hours:
        parts.append(f"{hours} ч")
    if mins:
        parts.append(f"{mins} мин")
    return " ".join(parts) or "менее минуты"


def _percent(rate: float | None) -> str:
    return "нет данных" if rate is None else f"{round(rate * 100)}%"


def format_stats(stats: Stats, period_label: str) -> str:
    avg = "нет данных" if stats.avg_processing is None else _format_duration(stats.avg_processing)
    return (
        f"Аналитика: {period_label}\n"
        f"Всего заявок: {stats.total}\n"
        f"Завершено: {stats.done}\n"
        f"Взято в работу: {_percent(stats.to_in_progress_rate)}\n"
        f"Доведено до завершения: {_percent(stats.to_done_rate)}\n"
        f"Среднее время обработки: {avg}"
    )


def page_buttons(page: int, total: int, page_size: int = PAGE_SIZE) -> list[tuple[str, int]]:
    """Кнопки пагинации: page с нуля, total это число записей."""
    pages = max((total + page_size - 1) // page_size, 1)
    buttons: list[tuple[str, int]] = []
    if page > 0:
        buttons.append(("‹", page - 1))
    if page < pages - 1:
        buttons.append(("›", page + 1))
    return buttons


# Клиентские сценарии
REQUEST_NOT_FOUND = "Заявка не найдена"
NO_REQUESTS = "У вас пока нет заявок"
MY_REQUESTS_TITLE = "Ваши заявки:"
ASK_NAME = "Как вас зовут? Выберите имя кнопкой или введите своё."
ASK_CONTACT = "Укажите телефон (например, +7 999 123-45-67) или username в формате @username."
ASK_DESCRIPTION = "Опишите, что вам нужно."
ASK_DATE = "Когда с вами связаться? Введите дату в формате ДД.ММ.ГГГГ или выберите кнопкой."
USE_BUTTONS = "Нажмите «Отправить» или «Отмена»."
REQUEST_ALREADY_HANDLED = "Заявка уже отправлена или отменена"
REQUEST_CREATED = "Заявка #{id} отправлена. Мы свяжемся с вами в указанный день."
REQUEST_UPDATED = "Заявка обновлена."
EDIT_NOT_ALLOWED = "Заявку уже взяли в работу, редактирование недоступно"
EDIT_CHOOSE_FIELD = "Что изменить в заявке #{id}?"
EDIT_PROMPTS = {
    "client_name": "Введите новое имя.",
    "contact": ASK_CONTACT,
    "description": ASK_DESCRIPTION,
    "preferred_contact_date": ASK_DATE,
}
EDIT_FIELD_LABELS = {
    "client_name": "Имя",
    "contact": "Контакт",
    "description": "Описание",
    "preferred_contact_date": "Дата связи",
}
BTN_EDIT = "Редактировать"
BTN_BACK = "К списку"
BTN_BACK_TO_CARD = "Назад"
BTN_SUBMIT = "Отправить"
BTN_TODAY = "Сегодня"
BTN_TOMORROW = "Завтра"


def format_confirm(name: str, contact: str, description: str, preferred: str) -> str:
    return (
        "Проверьте заявку:\n"
        f"Имя: {name}\n"
        f"Контакт: {contact}\n"
        f"Описание: {description}\n"
        f"Связаться: {preferred}"
    )


# Админские сценарии
FILTER_LABELS = {
    "": "Все",
    "new": "Новые",
    "in_progress": "В работе",
    "done": "Завершённые",
    "rejected": "Отклонённые",
}
PERIOD_LABELS = {
    "today": "Сегодня",
    "7d": "7 дней",
    "30d": "30 дней",
    "all": "Всё время",
}
ADMIN_REQUESTS_TITLE = "Заявки: {filter}"
ADMIN_SEARCH_TITLE = "Результаты поиска: {query}"
ADMIN_NO_REQUESTS = "Заявок нет: {filter}"
ADMIN_NOTHING_FOUND = "Ничего не найдено по запросу: {query}"
ASK_SEARCH = "Введите часть имени или контакта."
ASK_COMMENT = "Введите комментарий к заявке #{id}."
COMMENT_ADDED = "Комментарий добавлен."
STATUS_ALREADY_CHANGED = "Статус уже изменён, карточка обновлена"
STATUS_CHANGED = "Статус изменён: {status}"
CONFIRM_DELETE = "Удалить заявку #{id} безвозвратно? Вместе с ней удалятся комментарии и история статусов."
REQUEST_DELETED = "Заявка #{id} удалена"
BTN_COMMENT = "Комментарий"
BTN_DELETE = "Удалить"
BTN_DELETE_CONFIRM = "Да, удалить"
BTN_FILTER_ACTIVE = "• {label}"
ANALYTICS_CHOOSE_PERIOD = "Аналитика: выберите период."
REQUEST_SAVE_FAILED = "Не удалось сохранить заявку, попробуйте ещё раз"
EXPORT_CHOOSE_FORMAT = "Выберите формат выгрузки."
EXPORT_CHOOSE_PERIOD = "Выберите период выгрузки (по дате создания заявок)."
EXPORT_CHOOSE_STATUS = "Выберите статус заявок для выгрузки."
EXPORT_STATUS_ALL = "Все"
EXPORT_NOTHING = "Заявок по выбранным фильтрам нет"
EXPORT_BUILD_FAILED = "Не удалось сформировать файл, попробуйте ещё раз или обратитесь к разработчику"
EXPORT_SEND_FAILED = "Не удалось отправить файл, попробуйте ещё раз"
STALE_BUTTON = "Кнопка устарела, начните заново"
SEARCH_DONE = "Поиск выполнен."

# Запасные обработчики
STALE_CALLBACK = "Кнопка устарела. Откройте меню и повторите действие."
UNKNOWN_MESSAGE = "Не понял сообщение. Выберите действие в меню."
ERROR_GENERIC = "Что-то пошло не так. Попробуйте ещё раз."
