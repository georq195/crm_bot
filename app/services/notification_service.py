import logging
from typing import Protocol

from app.db.models import Request, Status, User
from app.labels import STATUS_LABELS
from app.services.analytics_service import SummaryCounts
from app.timeutil import to_local

logger = logging.getLogger(__name__)

MAX_LISTED = 20
MAX_MESSAGE_LEN = 4096
DESCRIPTION_LIMIT = 80
_DT_FORMAT = "%d.%m.%Y %H:%M"


class Sender(Protocol):
    async def send(self, tg_id: int, text: str, open_request_id: int | None = None) -> bool:
        """Не бросает исключений, возвращает False при сбое."""
        ...


def _shorten(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def format_request_brief(request: Request, tz: str) -> str:
    created = to_local(request.created_at, tz).strftime(_DT_FORMAT)
    return (
        f"#{request.id} {request.client_name}, {request.contact}\n"
        f"{_shorten(request.description, DESCRIPTION_LIMIT)}\n"
        f"Создана: {created}, связаться: {request.preferred_contact_date.strftime('%d.%m.%Y')}"
    )


def format_reminder(requests: list[Request], tz: str) -> str:
    header = f"Необработанные заявки: {len(requests)}"
    blocks = [format_request_brief(r, tz) for r in requests[:MAX_LISTED]]

    def assemble(shown: list[str]) -> str:
        rest = len(requests) - len(shown)
        parts = [header, *shown]
        if rest > 0:
            parts.append(f"и ещё {rest}")
        return "\n\n".join(parts)

    # Целые блоки, не влезающие в лимит, отбрасываем; «и ещё N» считает все непоказанные.
    shown: list[str] = []
    for block in blocks:
        if len(assemble([*shown, block])) > MAX_MESSAGE_LEN:
            break
        shown.append(block)
    if not shown and blocks:
        # Даже первый блок не влезает: режем его одного.
        overhead = len(assemble([""]))
        shown = [blocks[0][: MAX_MESSAGE_LEN - overhead - 1].rstrip() + "…"]
    return assemble(shown)


def format_daily_summary(counts: SummaryCounts, date_label: str) -> str:
    return (
        f"Сводка за {date_label}\n"
        f"Новых за сутки: {counts.new_24h}\n"
        f"В работе: {counts.in_progress}\n"
        f"Завершено за сутки: {counts.done_24h}\n"
        f"Новых без обработки более суток: {counts.stale_new}"
    )


async def _safe_send(sender: Sender, tg_id: int, text: str, open_request_id: int | None) -> None:
    try:
        await sender.send(tg_id, text, open_request_id)
    except Exception:
        logger.exception("Не удалось отправить уведомление пользователю %s", tg_id)


async def notify_new_request(
    sender: Sender, request: Request, author: User, admin_ids: list[int], tz: str
) -> None:
    try:
        text = "Новая заявка\n" + format_request_brief(request, tz)
        request_id = request.id
    except Exception:
        logger.exception("Не удалось сформировать уведомление о новой заявке")
        return
    for admin_id in admin_ids:
        if admin_id == author.tg_id:
            continue
        await _safe_send(sender, admin_id, text, request_id)


async def notify_status_changed(
    sender: Sender, request: Request, author: User, new_status: Status, changed_by_tg_id: int
) -> None:
    try:
        if changed_by_tg_id == author.tg_id:
            return
        text = f"Статус вашей заявки #{request.id} изменён: {STATUS_LABELS[new_status]}"
        await _safe_send(sender, author.tg_id, text, request.id)
    except Exception:
        logger.exception("Не удалось сформировать уведомление о смене статуса")
