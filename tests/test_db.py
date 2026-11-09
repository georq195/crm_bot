from datetime import date

from sqlalchemy import select, text

from app.db.models import Comment, Request, Role, Status, StatusHistory, User
from app.timeutil import utcnow


async def test_init_db_creates_four_tables(engine):
    async with engine.connect() as conn:
        rows = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        names = {r[0] for r in rows}
    assert {"users", "requests", "comments", "status_history"} <= names


async def test_foreign_keys_pragma_on(session):
    assert (await session.execute(text("PRAGMA foreign_keys"))).scalar() == 1


async def test_deleting_request_cascades_comments_and_history(session_factory):
    async with session_factory() as s:
        user = User(tg_id=1, username="u", full_name="U", role=Role.admin)
        s.add(user)
        await s.flush()
        req = Request(
            user_id=user.id, client_name="Иван", contact="+7", description="d",
            preferred_contact_date=date(2026, 11, 5), status=Status.new, search_text="иван +7",
        )
        s.add(req)
        await s.flush()
        req_id = req.id
        s.add(Comment(request_id=req_id, author_id=user.id, text="c"))
        s.add(StatusHistory(
            request_id=req_id, old_status=None, new_status=Status.new,
            changed_by=user.id, changed_at=utcnow(),
        ))
        await s.commit()

    # новая сессия: комментарии подгружаются вместе с заявкой (selectin)
    async with session_factory() as s:
        req = await s.get(Request, req_id)
        assert len(req.comments) == 1
        await s.delete(req)
        await s.commit()

    async with session_factory() as s:
        assert (await s.execute(select(Comment))).all() == []
        assert (await s.execute(select(StatusHistory))).all() == []
