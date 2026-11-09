from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.models import Comment, Request, Role, Status, StatusHistory
from app.db.repositories import users as users_repo
from app.services import request_service as svc
from app.services.validation import (
    InvalidInput,
    parse_preferred_date,
    validate_comment,
    validate_contact,
    validate_description,
    validate_name,
    validate_search_query,
)

NOW = datetime(2026, 11, 4, 12, 0, 0)
LATER = NOW + timedelta(hours=1)
PREF = date(2026, 11, 10)


async def make_user(session, tg_id=1, role=Role.client):
    user = await users_repo.get_or_create(session, tg_id, f"u{tg_id}", f"User {tg_id}", role)
    await session.commit()
    return user


async def make_request(session, author, **kw):
    args = dict(client_name="Иван Петров", contact="@Ivan_P", description="Нужен сайт",
                preferred_contact_date=PREF, now=NOW)
    args.update(kw)
    return await svc.create_request(session, author, **args)


async def history(session, request_id):
    rows = await session.scalars(
        select(StatusHistory).where(StatusHistory.request_id == request_id).order_by(StatusHistory.id)
    )
    return list(rows)


# ---------- validators ----------

def test_validate_name_strips_and_limits():
    assert validate_name("  Иван ") == "Иван"
    for bad in ("   ", "x" * 101):
        with pytest.raises(InvalidInput):
            validate_name(bad)


def test_validate_contact_normalizes_phone():
    assert validate_contact("+7 (999) 123-45-67") == "+79991234567"
    assert validate_contact("89991234567") == "89991234567"


def test_validate_contact_username():
    assert validate_contact(" @Ivan_P ") == "@Ivan_P"


def test_validate_contact_rejects_garbage():
    for bad in ("abc", "@iv", "123", "@" + "a" * 33, "@ива_нов", "+" + "1" * 16):
        with pytest.raises(InvalidInput):
            validate_contact(bad)


def test_validate_contact_rejects_non_ascii_digits():
    for bad in ("+\u0667\u0669\u0669\u0669\u0661\u0662\u0663\u0664\u0665\u0666\u0667",
                "+\uff17\uff19\uff19\uff19\uff11\uff12\uff13\uff14\uff15\uff16\uff17"):
        with pytest.raises(InvalidInput):
            validate_contact(bad)


def test_validate_contact_trailing_newline_is_stripped():
    assert validate_contact("+79991234567\n") == "+79991234567"
    assert validate_contact("@ivan_petrov\n") == "@ivan_petrov"


def test_validate_contact_idempotent():
    once = validate_contact("+7 (999) 123-45-67")
    assert validate_contact(once) == once


def test_validate_description_limit():
    assert validate_description("x" * 1000) == "x" * 1000
    with pytest.raises(InvalidInput):
        validate_description("x" * 1001)
    with pytest.raises(InvalidInput):
        validate_description("  ")


def test_validate_comment_limit():
    with pytest.raises(InvalidInput):
        validate_comment("x" * 301)


def test_validate_search_query():
    assert validate_search_query("  иван ") == "иван"
    for bad in ("  ", "x" * 101):
        with pytest.raises(InvalidInput):
            validate_search_query(bad)
    with pytest.raises(InvalidInput, match="Отправьте ответ текстом"):
        validate_search_query(None)


def test_validators_reject_none():
    for fn in (validate_name, validate_contact, validate_description, validate_comment):
        with pytest.raises(InvalidInput, match="Отправьте ответ текстом"):
            fn(None)
    with pytest.raises(InvalidInput, match="Отправьте ответ текстом"):
        parse_preferred_date(None, date(2026, 11, 4))


def test_parse_preferred_date():
    today = date(2026, 11, 4)
    assert parse_preferred_date("04.11.2026", today) == today
    assert parse_preferred_date(" 05.11.2026 ", today) == date(2026, 11, 5)
    for bad in ("31.02.2026", "03.11.2026", "2026-11-05", "завтра"):
        with pytest.raises(InvalidInput):
            parse_preferred_date(bad, today)


# ---------- create ----------

async def test_create_writes_history_and_search_text(session):
    author = await make_user(session)
    req = await make_request(session, author)
    assert req.status is Status.new
    assert req.search_text == svc.build_search_text("Иван Петров", "@Ivan_P")
    assert req.search_text == "иван петров @ivan_p"
    rows = await history(session, req.id)
    assert len(rows) == 1
    assert (rows[0].old_status, rows[0].new_status, rows[0].changed_by) == (None, Status.new, author.id)
    assert req.created_at == NOW


async def test_create_validates_and_normalizes(session):
    author = await make_user(session)
    req = await make_request(session, author, client_name="  Анна ", contact="+7 999 123 45 67")
    assert req.client_name == "Анна"
    assert req.contact == "+79991234567"
    with pytest.raises(InvalidInput):
        await make_request(session, author, contact="abc")


# ---------- status ----------

@pytest.mark.parametrize("start,target", [
    (Status.new, Status.in_progress),
    (Status.new, Status.rejected),
    (Status.in_progress, Status.done),
    (Status.in_progress, Status.rejected),
])
async def test_valid_transitions(session, start, target):
    author = await make_user(session)
    admin = await make_user(session, 2, Role.admin)
    req = await make_request(session, author)
    if start is Status.in_progress:
        await svc.change_status(session, req.id, Status.in_progress, admin, now=NOW)
    updated = await svc.change_status(session, req.id, target, admin, now=LATER)
    assert updated.status is target
    assert updated.updated_at == LATER
    if target in (Status.done, Status.rejected):
        assert updated.closed_at == LATER
    else:
        assert updated.closed_at is None
    last = (await history(session, req.id))[-1]
    assert (last.old_status, last.new_status, last.changed_by) == (start, target, admin.id)


async def test_invalid_transitions_raise(session):
    author = await make_user(session)
    admin = await make_user(session, 2, Role.admin)
    req = await make_request(session, author)
    with pytest.raises(svc.InvalidTransition):
        await svc.change_status(session, req.id, Status.done, admin)
    await svc.change_status(session, req.id, Status.in_progress, admin)
    await svc.change_status(session, req.id, Status.done, admin)
    with pytest.raises(svc.InvalidTransition):
        await svc.change_status(session, req.id, Status.in_progress, admin)
    req2 = await make_request(session, author)
    await svc.change_status(session, req2.id, Status.rejected, admin)
    with pytest.raises(svc.InvalidTransition):
        await svc.change_status(session, req2.id, Status.new, admin)


async def test_double_status_change_does_not_duplicate_history(session):
    author = await make_user(session)
    admin = await make_user(session, 2, Role.admin)
    req = await make_request(session, author)
    await svc.change_status(session, req.id, Status.in_progress, admin)
    with pytest.raises(svc.InvalidTransition):
        await svc.change_status(session, req.id, Status.in_progress, admin)
    assert len(await history(session, req.id)) == 2


async def test_change_status_missing_raises(session):
    admin = await make_user(session, 2, Role.admin)
    with pytest.raises(svc.RequestNotFound):
        await svc.change_status(session, 999, Status.in_progress, admin)


# ---------- edit ----------

async def test_edit_by_author_in_new_updates_search_text(session):
    author = await make_user(session)
    req = await make_request(session, author)
    edited = await svc.edit_request(
        session, req.id, author, now=LATER,
        client_name=" Пётр Сидоров ", contact="+7 (999) 000-11-22",
        description="Новое описание", preferred_contact_date=date(2026, 12, 1),
    )
    assert edited.client_name == "Пётр Сидоров"
    assert edited.contact == "+79990001122"
    assert edited.description == "Новое описание"
    assert edited.preferred_contact_date == date(2026, 12, 1)
    assert edited.search_text == "пётр сидоров +79990001122"
    assert edited.updated_at == LATER


async def test_edit_single_field_keeps_search_text_consistent(session):
    author = await make_user(session)
    req = await make_request(session, author)
    edited = await svc.edit_request(session, req.id, author, client_name="Олег")
    assert edited.search_text == "олег @ivan_p"


async def test_edit_invalid_value_raises(session):
    author = await make_user(session)
    req = await make_request(session, author)
    with pytest.raises(InvalidInput):
        await svc.edit_request(session, req.id, author, contact="abc")


async def test_edit_after_in_progress_rejected(session):
    author = await make_user(session)
    admin = await make_user(session, 2, Role.admin)
    req = await make_request(session, author)
    await svc.change_status(session, req.id, Status.in_progress, admin)
    with pytest.raises(svc.EditNotAllowed):
        await svc.edit_request(session, req.id, author, description="Другое")


async def test_edit_race_status_changes_after_card_was_shown(session, session_factory):
    author = await make_user(session)
    admin = await make_user(session, 2, Role.admin)
    req = await make_request(session, author)
    # клиент получил карточку: объект загружен в identity map исходной сессии
    shown = await svc.get_request_for_actor(session, req.id, author)
    assert shown.status is Status.new

    async with session_factory() as other:
        other_admin = await other.get(type(admin), admin.id)
        await svc.change_status(other, req.id, Status.in_progress, other_admin, now=LATER)

    with pytest.raises(svc.EditNotAllowed):
        await svc.edit_request(session, req.id, author, description="Поздно")
    fresh = await svc.get_request_for_actor(session, req.id, author)
    assert fresh.description == "Нужен сайт"


async def test_edit_by_foreign_client_or_admin_rejected(session):
    author = await make_user(session)
    stranger = await make_user(session, 3)
    admin = await make_user(session, 2, Role.admin)
    req = await make_request(session, author)
    for actor in (stranger, admin):
        with pytest.raises(svc.EditNotAllowed):
            await svc.edit_request(session, req.id, actor, description="Взлом")


async def test_edit_missing_raises(session):
    author = await make_user(session)
    with pytest.raises(svc.RequestNotFound):
        await svc.edit_request(session, 999, author, description="x")


# ---------- get / list ----------

async def test_get_request_for_actor_hides_foreign_request(session):
    author = await make_user(session)
    stranger = await make_user(session, 3)
    admin = await make_user(session, 2, Role.admin)
    req = await make_request(session, author)
    with pytest.raises(svc.RequestNotFound):
        await svc.get_request_for_actor(session, req.id, stranger)
    with pytest.raises(svc.RequestNotFound):
        await svc.get_request_for_actor(session, 999, admin)
    assert (await svc.get_request_for_actor(session, req.id, admin)).id == req.id
    assert (await svc.get_request_for_actor(session, req.id, author)).id == req.id


async def test_list_requests_client_sees_only_own(session):
    a = await make_user(session)
    b = await make_user(session, 3)
    admin = await make_user(session, 2, Role.admin)
    await make_request(session, a)
    await make_request(session, a)
    await make_request(session, b)
    own, total = await svc.list_requests(session, a)
    assert total == 2 and all(r.user_id == a.id for r in own)
    everything, total_all = await svc.list_requests(session, admin)
    assert total_all == 3 and len(everything) == 3


async def test_list_requests_filters_and_pages(session):
    a = await make_user(session)
    admin = await make_user(session, 2, Role.admin)
    r1 = await make_request(session, a, client_name="Алиса", now=NOW)
    for i in range(5):
        await make_request(session, a, client_name=f"Боб{i}", now=NOW + timedelta(minutes=i + 1))
    await svc.change_status(session, r1.id, Status.in_progress, admin)
    found, total = await svc.list_requests(session, admin, query="алис")
    assert [r.id for r in found] == [r1.id] and total == 1
    in_prog, total = await svc.list_requests(session, admin, status=Status.in_progress)
    assert total == 1
    page0, total = await svc.list_requests(session, admin, page=0, page_size=5)
    page1, _ = await svc.list_requests(session, admin, page=1, page_size=5)
    assert total == 6 and len(page0) == 5 and len(page1) == 1


# ---------- comments / delete ----------

async def test_add_comment_validates_length(session):
    author = await make_user(session)
    admin = await make_user(session, 2, Role.admin)
    req = await make_request(session, author)
    with pytest.raises(InvalidInput):
        await svc.add_comment(session, req.id, admin, "x" * 301)
    comment = await svc.add_comment(session, req.id, admin, " Позвонили ", now=LATER)
    assert comment.text == "Позвонили" and comment.created_at == LATER
    with pytest.raises(svc.RequestNotFound):
        await svc.add_comment(session, 999, admin, "текст")


async def test_delete_removes_comments_and_history(session):
    author = await make_user(session)
    admin = await make_user(session, 2, Role.admin)
    req = await make_request(session, author)
    await svc.add_comment(session, req.id, admin, "коммент")
    await svc.change_status(session, req.id, Status.in_progress, admin)
    rid = req.id
    await svc.delete_request(session, rid)
    assert await session.scalar(select(Request).where(Request.id == rid)) is None
    assert (await session.scalars(select(Comment).where(Comment.request_id == rid))).all() == []
    assert await history(session, rid) == []


async def test_delete_missing_raises(session):
    with pytest.raises(svc.RequestNotFound):
        await svc.delete_request(session, 999)


# ---------- atomicity of status guard ----------

def _use_stale(monkeypatch, stale):
    """Проверка статуса «уже прошла» по устаревшему объекту другой сессии."""
    async def fake(_session, _request_id):
        return stale

    monkeypatch.setattr(svc, "_get_fresh", fake)


async def test_concurrent_change_status_does_not_duplicate_history(
    session, session_factory, monkeypatch
):
    author = await make_user(session)
    admin = await make_user(session, 2, Role.admin)
    req = await make_request(session, author)
    async with session_factory() as other:
        stale = await other.get(Request, req.id)  # вторая сессия увидела status=new
        assert stale.status is Status.new
        await svc.change_status(session, req.id, Status.in_progress, admin, now=LATER)
        _use_stale(monkeypatch, stale)
        with pytest.raises(svc.InvalidTransition):
            await svc.change_status(other, req.id, Status.in_progress, admin, now=LATER)
    monkeypatch.undo()
    assert len(await history(session, req.id)) == 2


async def test_edit_after_concurrent_status_change_is_rejected_at_write(
    session, session_factory, monkeypatch
):
    author = await make_user(session)
    admin = await make_user(session, 2, Role.admin)
    req = await make_request(session, author)
    async with session_factory() as other:
        stale = await other.get(Request, req.id)
        assert stale.status is Status.new
        await svc.change_status(session, req.id, Status.in_progress, admin, now=LATER)
        _use_stale(monkeypatch, stale)
        with pytest.raises(svc.EditNotAllowed):
            await svc.edit_request(other, req.id, author, description="Поздно")
    monkeypatch.undo()
    fresh = await svc.get_request_for_actor(session, req.id, author)
    assert fresh.description == "Нужен сайт"


async def test_can_edit_only_for_author_of_new_request(session):
    author = await make_user(session)
    stranger = await make_user(session, 3)
    admin = await make_user(session, 2, Role.admin)
    req = await make_request(session, author)

    assert svc.can_edit(req, author) is True
    assert svc.can_edit(req, admin) is False
    assert svc.can_edit(req, stranger) is False

    await svc.change_status(session, req.id, Status.in_progress, admin)
    req = await svc.get_request_for_actor(session, req.id, author)
    assert svc.can_edit(req, author) is False
