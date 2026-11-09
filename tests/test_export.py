import csv
import re
from datetime import date, datetime

import pytest
from openpyxl import load_workbook

from app.db.models import Role, Status
from app.db.repositories import users as users_repo
from app.services import request_service
from app.services.export_service import COLUMNS, build_export, sanitize_cell

TZ = "Europe/Moscow"
T1 = datetime(2026, 11, 1, 21, 30)  # 02.11.2026 00:30 МСК
T2 = datetime(2026, 11, 5, 12, 0)


@pytest.fixture
async def people(session):
    author = await users_repo.get_or_create(session, 1, "client", "Client", Role.client)
    admin = await users_repo.get_or_create(session, 2, "admin", "Admin", Role.admin)
    await session.commit()
    return author, admin


async def make(session, people, created, description="Нужна консультация", contact="+7 900 000-00-00"):
    author, _ = people
    return await request_service.create_request(
        session, author, "Иван", contact, description, date(2026, 11, 10), now=created
    )


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))


def test_sanitize_cell():
    assert sanitize_cell("=SUM(A1)") == "'=SUM(A1)"
    assert sanitize_cell("-1") == "'-1"
    assert sanitize_cell("@x") == "'@x"
    assert sanitize_cell("+1+1") == "'+1+1"
    assert sanitize_cell("+79991234567") == "+79991234567"
    assert sanitize_cell("Иван") == "Иван"
    assert sanitize_cell("") == ""


def test_sanitize_cell_tab_cr_and_trailing_newline():
    assert sanitize_cell("\t=1+1") == "'\t=1+1"
    assert sanitize_cell("\r=1+1") == "'\r=1+1"
    assert sanitize_cell("+79991234567\n") == "'+79991234567\n"
    assert sanitize_cell("+79991234567") == "+79991234567"
    assert sanitize_cell("Иван") == "Иван"


def test_sanitize_cell_valid_username_exempt_invalid_prefixed():
    assert sanitize_cell("@ivan_petrov") == "@ivan_petrov"
    assert sanitize_cell("@" + "a" * 32) == "@" + "a" * 32
    for bad in ("@x", "@SUM(A1)", "@a b", "@ivan_petrov\n", "@" + "a" * 33, "@иван_петров"):
        assert sanitize_cell(bad) == "'" + bad


async def test_csv_username_contact_has_no_apostrophe(session, people, tmp_path):
    await make(session, people, T1, contact="@ivan_petrov")
    await session.commit()
    path = await build_export(
        session, "csv", date_from=None, date_to=None, status=None, tz=TZ, out_dir=tmp_path
    )
    assert read_csv(path)[1][2] == "@ivan_petrov"


async def test_csv_has_bom_header_and_local_dates(session, people, tmp_path):
    _, admin = people
    req = await make(session, people, T1, contact="+7 900 000-00-00")
    await request_service.change_status(session, req.id, Status.in_progress, admin, now=T2)
    await request_service.change_status(session, req.id, Status.done, admin, now=T2)
    await session.commit()

    path = await build_export(
        session, "csv", date_from=None, date_to=None, status=None, tz=TZ, out_dir=tmp_path / "out"
    )
    assert path is not None and path.suffix == ".csv" and path.name.startswith("requests_")
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    rows = read_csv(path)
    assert rows[0] == COLUMNS
    assert rows[1] == [
        str(req.id), "Иван", "+79000000000", "Нужна консультация",
        "10.11.2026", "Завершена", "02.11.2026 00:30", "05.11.2026 15:00",
    ]


async def test_csv_sanitizes_formula_cells(session, people, tmp_path):
    await make(session, people, T1, description="=1+1")
    await session.commit()
    path = await build_export(
        session, "csv", date_from=None, date_to=None, status=None, tz=TZ, out_dir=tmp_path
    )
    assert read_csv(path)[1][3] == "'=1+1"


async def test_xlsx_header_bold_formula_text_stays_string(session, people, tmp_path):
    req = await make(session, people, T1, description="=1+1")
    await session.commit()
    path = await build_export(
        session, "xlsx", date_from=None, date_to=None, status=None, tz=TZ, out_dir=tmp_path
    )
    assert path is not None and path.suffix == ".xlsx"
    ws = load_workbook(path).active
    assert [c.value for c in ws[1]] == COLUMNS
    assert all(c.font.bold for c in ws[1])
    cell = ws.cell(row=2, column=COLUMNS.index("Описание") + 1)
    assert cell.value == "=1+1"
    assert cell.data_type == "s"
    assert ws.cell(row=2, column=1).value == req.id
    assert ws.column_dimensions["D"].width > 8
    assert ws.cell(row=2, column=COLUMNS.index("Закрыта") + 1).value in (None, "")


async def test_export_filters_by_status_and_period(session, people, tmp_path):
    _, admin = people
    a = await make(session, people, datetime(2026, 11, 1, 10, 0))
    b = await make(session, people, datetime(2026, 11, 3, 10, 0))
    c = await make(session, people, datetime(2026, 11, 5, 10, 0))
    await request_service.change_status(session, b.id, Status.rejected, admin, now=T2)
    await session.commit()

    runs = iter(range(100))

    async def ids(**kw):
        params = dict(date_from=None, date_to=None, status=None) | kw
        path = await build_export(session, "csv", tz=TZ, out_dir=tmp_path / f"run{next(runs)}", **params)
        return [int(r[0]) for r in read_csv(path)[1:]]

    assert await ids() == [a.id, b.id, c.id]
    assert await ids(status=Status.rejected) == [b.id]
    # from inclusive, to exclusive
    assert await ids(date_from=b.created_at, date_to=c.created_at) == [b.id]


async def test_export_empty_returns_none_and_creates_no_file(session, tmp_path):
    out = tmp_path / "out"
    assert await build_export(
        session, "xlsx", date_from=None, date_to=None, status=None, tz=TZ, out_dir=out
    ) is None
    assert not out.exists()


@pytest.mark.parametrize("fmt", ["csv", "xlsx"])
async def test_control_characters_are_stripped_from_text_cells(session, people, tmp_path, fmt):
    await make(session, people, T1, description="до\x0bпосле\x00\x1f\ttab\nline\r")
    await session.commit()

    path = await build_export(
        session, fmt, date_from=None, date_to=None, status=None, tz=TZ, out_dir=tmp_path / "out"
    )

    assert path is not None
    if fmt == "csv":
        description = read_csv(path)[1][3]
    else:
        description = load_workbook(path).active.cell(row=2, column=4).value
    assert description.replace("\r\n", "\n").rstrip("\r") == "допосле\ttab\nline"


@pytest.mark.parametrize("fmt", ["csv", "xlsx"])
async def test_same_second_exports_get_distinct_files(session, people, tmp_path, fmt, monkeypatch):
    monkeypatch.setattr("app.services.export_service.utcnow", lambda: datetime(2026, 11, 5, 12, 0, 0))
    await make(session, people, T1)
    await session.commit()
    kwargs = dict(date_from=None, date_to=None, status=None, tz=TZ, out_dir=tmp_path)
    first = await build_export(session, fmt, **kwargs)
    second = await build_export(session, fmt, **kwargs)
    assert first != second
    assert first.exists() and second.exists()
    for path in (first, second):
        assert re.fullmatch(rf"requests_20261105_120000_[0-9a-f]{{6}}\.{fmt}", path.name)
