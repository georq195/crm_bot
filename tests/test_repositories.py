from datetime import date, datetime, timedelta

from sqlalchemy import func, select

from app.db.models import Comment, Request, Role, Status, StatusHistory
from app.db.repositories import comments as comments_repo
from app.db.repositories import requests as requests_repo
from app.db.repositories import users as users_repo

NOW = datetime(2026, 11, 4, 12, 0, 0)


async def make_user(session, tg_id=1, role=Role.client):
    user = await users_repo.get_or_create(session, tg_id, "u%d" % tg_id, "User %d" % tg_id, role)
    await session.flush()
    return user


async def make_request(session, user, *, name="Иван Петров", contact="@ivan", search=None,
                       now=NOW, description="Описание"):
    return await requests_repo.add(
        session, user.id, name, contact, description, date(2026, 11, 10),
        search if search is not None else f"{name} {contact} {description}".lower(), now,
    )


async def test_get_or_create_updates_role(session):
    first = await users_repo.get_or_create(session, 100, "old", "Old Name", Role.client)
    await session.flush()
    second = await users_repo.get_or_create(session, 100, "new", "New Name", Role.admin)
    assert second.id == first.id
    assert second.role == Role.admin
    assert second.username == "new"
    assert second.full_name == "New Name"


async def test_list_page_only_user_and_total(session):
    u1 = await make_user(session, 1)
    u2 = await make_user(session, 2)
    for i in range(3):
        await make_request(session, u1, now=NOW + timedelta(minutes=i))
    await make_request(session, u2)
    items, total = await requests_repo.list_page(session, user_id=u1.id, offset=0, limit=2)
    assert len(items) == 2
    assert total == 3
    assert all(r.user_id == u1.id for r in items)


async def test_list_page_status_filter_and_order(session):
    user = await make_user(session)
    a = await make_request(session, user, now=NOW)
    b = await make_request(session, user, now=NOW + timedelta(minutes=1))
    c = await make_request(session, user, now=NOW + timedelta(minutes=2))
    b.status = Status.in_progress
    await session.flush()
    items, total = await requests_repo.list_page(session)
    assert [r.id for r in items] == [c.id, b.id, a.id]
    assert total == 3
    items, total = await requests_repo.list_page(session, status=Status.new)
    assert [r.id for r in items] == [c.id, a.id]
    assert total == 2


async def test_search_cyrillic_case_insensitive(session):
    user = await make_user(session)
    await make_request(session, user, name="Иван Петров", search="иван петров")
    await make_request(session, user, name="Анна", search="анна")
    items, total = await requests_repo.list_page(session, query="ИВАН")
    assert [r.client_name for r in items] == ["Иван Петров"]
    assert total == 1


async def test_search_by_contact(session):
    user = await make_user(session)
    await make_request(session, user, contact="@ivan")
    await make_request(session, user, contact="@anna")
    items, total = await requests_repo.list_page(session, query="@ivan")
    assert [r.contact for r in items] == ["@ivan"]
    assert total == 1


async def test_search_wildcards_are_literal(session):
    user = await make_user(session)
    await make_request(session, user, search="обычный текст")
    await make_request(session, user, search="скидка 50% и a_b и back\\slash")
    for q, expected in (("%", 1), ("_", 1), ("\\", 1), ("50%", 1), ("a_b", 1)):
        items, total = await requests_repo.list_page(session, query=q)
        assert total == expected, q
        assert len(items) == expected, q
    items, total = await requests_repo.list_page(session, query="a_c")
    assert total == 0


async def test_list_due_for_reminder(session):
    user = await make_user(session)
    old = NOW - timedelta(hours=48)
    due = await make_request(session, user, now=old)
    fresh = await make_request(session, user, now=NOW)
    busy = await make_request(session, user, now=old)
    busy.status = Status.in_progress
    recent = await make_request(session, user, now=old)
    recent.last_reminded_at = NOW - timedelta(hours=1)
    stale = await make_request(session, user, now=old)
    stale.last_reminded_at = NOW - timedelta(hours=30)
    await session.flush()
    result = await requests_repo.list_due_for_reminder(
        session, created_before=NOW - timedelta(hours=24), remind_before=NOW - timedelta(hours=24),
    )
    ids = {r.id for r in result}
    assert ids == {due.id, stale.id}
    assert fresh.id not in ids


async def test_list_all_filters_by_status_and_dates(session):
    user = await make_user(session)
    a = await make_request(session, user, now=NOW)
    b = await make_request(session, user, now=NOW + timedelta(days=1))
    b.status = Status.done
    await session.flush()
    assert {r.id for r in await requests_repo.list_all(session)} == {a.id, b.id}
    assert [r.id for r in await requests_repo.list_all(session, status=Status.done)] == [b.id]
    got = await requests_repo.list_all(
        session, created_from=NOW + timedelta(hours=1), created_to=NOW + timedelta(days=2),
    )
    assert [r.id for r in got] == [b.id]
    # верхняя граница исключается, нижняя включается
    got = await requests_repo.list_all(session, created_from=NOW, created_to=b.created_at)
    assert [r.id for r in got] == [a.id]


async def test_delete_removes_request(session):
    user = await make_user(session)
    req = await make_request(session, user)
    await comments_repo.add(session, req.id, user.id, "коммент", NOW)
    await requests_repo.add_history(session, req.id, None, Status.new, user.id, NOW)
    await session.commit()
    session.expunge_all()

    fetched = await requests_repo.get(session, req.id)
    assert len(fetched.comments) == 1
    assert await requests_repo.delete(session, req.id) is True
    await session.commit()
    session.expunge_all()

    assert await requests_repo.get(session, req.id) is None
    assert await session.scalar(select(func.count()).select_from(Request)) == 0
    assert await session.scalar(select(func.count()).select_from(Comment)) == 0
    assert await session.scalar(select(func.count()).select_from(StatusHistory)) == 0
    assert await requests_repo.delete(session, req.id) is False


async def test_comments_added_in_order(session):
    user = await make_user(session)
    req = await make_request(session, user)
    await comments_repo.add(session, req.id, user.id, "первый", NOW)
    await comments_repo.add(session, req.id, user.id, "второй", NOW + timedelta(minutes=1))
    result = await comments_repo.list_for_request(session, req.id)
    assert [c.text for c in result] == ["первый", "второй"]
