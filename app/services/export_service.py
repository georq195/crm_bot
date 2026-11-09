import csv
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Request, Status
from app.db.repositories import requests as requests_repo
from app.labels import STATUS_LABELS
from app.timeutil import to_local, utcnow

COLUMNS = ["ID", "Имя", "Контакт", "Описание", "Желаемая дата", "Статус", "Создана", "Закрыта"]

_PHONE_RE = re.compile(r"\+?[0-9]{10,15}")
_USERNAME_RE = re.compile(r"@[A-Za-z0-9_]{5,32}")
_ILLEGAL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_DT_FORMAT = "%d.%m.%Y %H:%M"
_DATE_FORMAT = "%d.%m.%Y"
_MAX_WIDTH = 60


def sanitize_cell(value: str) -> str:
    """Защита CSV от формул: префикс ' (нормализованные телефоны и @username не трогаем)."""
    if (
        value.startswith(_FORMULA_PREFIXES)
        and not _PHONE_RE.fullmatch(value)
        and not _USERNAME_RE.fullmatch(value)
    ):
        return "'" + value
    return value


def _clean(value: str | int) -> str | int:
    """Убирает управляющие символы, недопустимые в XML/XLSX (\\t, \\n, \\r остаются)."""
    return _ILLEGAL_CHARS_RE.sub("", value) if isinstance(value, str) else value


def _row(request: Request, tz: str) -> list[str | int]:
    closed = request.closed_at
    return [_clean(v) for v in [
        request.id,
        request.client_name,
        request.contact,
        request.description,
        request.preferred_contact_date.strftime(_DATE_FORMAT),
        STATUS_LABELS[request.status],
        to_local(request.created_at, tz).strftime(_DT_FORMAT),
        to_local(closed, tz).strftime(_DT_FORMAT) if closed else "",
    ]]


def _write_csv(path: Path, rows: list[list[str | int]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        for row in rows:
            writer.writerow([sanitize_cell(v) if isinstance(v, str) else v for v in row])


def _write_xlsx(path: Path, rows: list[list[str | int]]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(COLUMNS)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append(row)
    # Все текстовые ячейки принудительно строками: формулой ничто не станет.
    for row_cells in ws.iter_rows(min_row=2):
        for cell in row_cells:
            if isinstance(cell.value, str):
                cell.data_type = "s"
    for idx, title in enumerate(COLUMNS, start=1):
        longest = max(
            [len(title)]
            + [max(len(line) for line in str(r[idx - 1]).split("\n")) for r in rows]
        )
        ws.column_dimensions[get_column_letter(idx)].width = min(longest + 2, _MAX_WIDTH)
    wb.save(path)


async def build_export(
    session: AsyncSession,
    fmt: Literal["csv", "xlsx"],
    *,
    date_from: datetime | None,
    date_to: datetime | None,
    status: Status | None,
    tz: str,
    out_dir: Path,
) -> Path | None:
    requests = await requests_repo.list_all(
        session, status=status, created_from=date_from, created_to=date_to
    )
    if not requests:
        return None
    rows = [_row(r, tz) for r in requests]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"requests_{utcnow():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}.{fmt}"
    try:
        if fmt == "csv":
            _write_csv(path, rows)
        else:
            _write_xlsx(path, rows)
    except Exception:
        path.unlink(missing_ok=True)  # недописанный файл не оставляем
        raise
    return path
