"""Inline- и reply-клавиатуры заявок."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot import texts
from app.bot.callbacks import ExportCB, PeriodCB, RequestCB
from app.db.models import Request, Status
from app.labels import STATUS_LABELS
from app.services.request_service import ALLOWED_TRANSITIONS

_LABEL_LIMIT = 40


def _short(text: str, limit: int = _LABEL_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def request_list_kb(requests: list[Request], page: int, total: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for r in requests:
        label = _short(f"#{r.id} {STATUS_LABELS[r.status]} · {r.client_name}")
        builder.button(text=label, callback_data=RequestCB(action="open", id=r.id, page=page))
    builder.adjust(1)
    nav = texts.page_buttons(page, total)
    if nav:
        builder.row(
            *(
                InlineKeyboardButton(text=label, callback_data=RequestCB(action="list", page=target).pack())
                for label, target in nav
            )
        )
    return builder.as_markup()


def confirm_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_SUBMIT, callback_data=RequestCB(action="submit"))
    builder.button(text=texts.BTN_CANCEL, callback_data=RequestCB(action="cancel"))
    builder.adjust(2)
    return builder.as_markup()


def date_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_TODAY, callback_data=RequestCB(action="date", status="today"))
    builder.button(text=texts.BTN_TOMORROW, callback_data=RequestCB(action="date", status="tomorrow"))
    builder.adjust(2)
    return builder.as_markup()


def client_card_kb(request: Request, page: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if request.status == Status.new:
        builder.button(text=texts.BTN_EDIT, callback_data=RequestCB(action="edit", id=request.id, page=page))
    builder.button(text=texts.BTN_BACK, callback_data=RequestCB(action="list", page=page))
    builder.adjust(1)
    return builder.as_markup()


def edit_fields_kb(request_id: int, page: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for field, label in texts.EDIT_FIELD_LABELS.items():
        builder.button(text=label, callback_data=RequestCB(action="ef", id=request_id, page=page, status=field))
    builder.button(text=texts.BTN_BACK_TO_CARD, callback_data=RequestCB(action="open", id=request_id, page=page))
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def cancel_kb(*first_row: str) -> ReplyKeyboardMarkup:
    """Reply-клавиатура с «Отмена» (и, при необходимости, кнопками подсказок над ней)."""
    rows = [[KeyboardButton(text=t)] for t in first_row if t]
    rows.append([KeyboardButton(text=texts.BTN_CANCEL)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def status_filter_kb(active: str | None) -> InlineKeyboardMarkup:
    """Ряд фильтров по статусу; active: код фильтра («» для «Все»), он отмечен точкой; None: не отмечен никто (результаты поиска)."""
    builder = InlineKeyboardBuilder()
    for code, label in texts.FILTER_LABELS.items():
        text = texts.BTN_FILTER_ACTIVE.format(label=label) if code == active else label
        builder.button(text=text, callback_data=RequestCB(action="filter", status=code))
    builder.adjust(3, 2)
    return builder.as_markup()


def admin_list_kb(requests: list[Request], page: int, total: int, active: str | None) -> InlineKeyboardMarkup:
    """Список заявок с пагинацией и фильтрами под ним."""
    rows = request_list_kb(requests, page, total).inline_keyboard if requests else []
    return InlineKeyboardMarkup(inline_keyboard=[*rows, *status_filter_kb(active).inline_keyboard])


def card_kb(request: Request, page: int = 0) -> InlineKeyboardMarkup:
    """Карточка админа; кнопки смены статуса берутся из ALLOWED_TRANSITIONS."""
    builder = InlineKeyboardBuilder()
    targets = [s for s in Status if s in ALLOWED_TRANSITIONS[request.status]]
    for target in targets:
        builder.button(
            text=f"→ {STATUS_LABELS[target]}",
            callback_data=RequestCB(action="status", id=request.id, page=page, status=target.value),
        )
    builder.button(text=texts.BTN_COMMENT, callback_data=RequestCB(action="comment", id=request.id, page=page))
    builder.button(text=texts.BTN_DELETE, callback_data=RequestCB(action="delete", id=request.id, page=page))
    builder.button(text=texts.BTN_BACK, callback_data=RequestCB(action="list", page=page))
    builder.adjust(*([len(targets)] if targets else []), 2, 1)
    return builder.as_markup()


def delete_confirm_kb(request_id: int, page: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_DELETE_CONFIRM, callback_data=RequestCB(action="delete_ok", id=request_id, page=page))
    builder.button(text=texts.BTN_CANCEL, callback_data=RequestCB(action="open", id=request_id, page=page))
    builder.adjust(2)
    return builder.as_markup()


def period_kb(kind: str) -> InlineKeyboardMarkup:
    """kind: «stats» для аналитики или формат выгрузки («csv», «xlsx»)."""
    builder = InlineKeyboardBuilder()
    for period, label in texts.PERIOD_LABELS.items():
        builder.button(text=label, callback_data=PeriodCB(kind=kind, period=period))
    builder.adjust(2, 2)
    return builder.as_markup()


def export_format_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for fmt in ("csv", "xlsx"):
        builder.button(text=fmt.upper(), callback_data=ExportCB(fmt=fmt, period="", status=""))
    builder.adjust(2)
    return builder.as_markup()


def export_status_kb(fmt: str, period: str) -> InlineKeyboardMarkup:
    """Выбор статуса: те же статусы, что в фильтрах, плюс «Все» (код all)."""
    builder = InlineKeyboardBuilder()
    for code, label in texts.FILTER_LABELS.items():
        builder.button(
            text=texts.EXPORT_STATUS_ALL if not code else label,
            callback_data=ExportCB(fmt=fmt, period=period, status=code or "all"),
        )
    builder.adjust(3, 2)
    return builder.as_markup()
