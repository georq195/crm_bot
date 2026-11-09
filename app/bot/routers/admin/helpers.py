"""Общие вспомогательные функции админских роутеров (без бизнес-правил)."""
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts
from app.bot.keyboards.requests import admin_list_kb, status_filter_kb
from app.db.models import Request, Status, User
from app.services import request_service


def card_text(request: Request, tz: str) -> str:
    return texts.format_request_card(request, request.comments, for_admin=True, tz=tz)


def _status_filter(code: str) -> Status | None:
    try:
        return Status(code) if code else None
    except ValueError:
        return None


async def render_list(
    session: AsyncSession, user: User, state: FSMContext, page: int
) -> tuple[str, InlineKeyboardMarkup]:
    """Список по контексту из FSM: поисковый запрос (если есть) или фильтр по статусу."""
    data = await state.get_data()
    query: str | None = data.get("query")
    code = "" if query else data.get("flt", "")
    status = _status_filter(code)
    if status is None:
        code = ""

    async def fetch(p: int):
        return await request_service.list_requests(session, user, status=status, query=query, page=p)

    requests, total = await fetch(page)
    if total and not requests:  # страница за пределами списка: последняя
        page = (total - 1) // texts.PAGE_SIZE
        requests, total = await fetch(page)

    if query:
        title = (
            texts.ADMIN_SEARCH_TITLE.format(query=query)
            if total
            else texts.ADMIN_NOTHING_FOUND.format(query=query)
        )
    else:
        label = texts.FILTER_LABELS[code]
        title = texts.ADMIN_REQUESTS_TITLE.format(filter=label) if total else texts.ADMIN_NO_REQUESTS.format(filter=label)
    active = None if query else code  # в результатах поиска ни один фильтр не активен
    if not total:
        return title, status_filter_kb(active)
    return title, admin_list_kb(requests, page, total, active)
