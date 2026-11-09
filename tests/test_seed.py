from datetime import timedelta

from sqlalchemy import select

from app.db.models import Request, Status, StatusHistory
from app.db.repositories import requests as requests_repo
from app.seed import seed, seed_if_empty
from app.services.analytics_service import compute
from app.services.request_service import CLOSED_STATUSES


async def test_seed_covers_all_statuses_and_matches_analytics(session):
    n = await seed(session)
    stats = await compute(session, None, None)
    requests = await requests_repo.list_all(session)

    assert n == len(requests) >= 10
    assert stats.total == n
    assert {r.status for r in requests} == set(Status)
    assert any(r.comments for r in requests)
    spread = {r.created_at.date() for r in requests}
    assert len(spread) >= 5
    assert max(spread) - min(spread) >= timedelta(days=5)


async def test_seed_history_and_closed_at_are_consistent(session):
    await seed(session)
    requests = await requests_repo.list_all(session)
    for r in requests:
        rows = list(await session.scalars(
            select(StatusHistory).where(StatusHistory.request_id == r.id).order_by(StatusHistory.id)
        ))
        assert rows[0].old_status is None and rows[0].new_status == Status.new
        assert rows[0].changed_at == r.created_at
        assert rows[-1].new_status == r.status
        for prev, cur in zip(rows, rows[1:]):
            assert cur.old_status == prev.new_status
            assert cur.changed_at >= prev.changed_at
        if r.status in CLOSED_STATUSES:
            assert r.closed_at == rows[-1].changed_at
        else:
            assert r.closed_at is None
        if r.status != Status.new:
            assert len(rows) >= 2


async def test_seed_if_empty_does_not_duplicate(session):
    first = await seed_if_empty(session)
    second = await seed_if_empty(session)
    assert first > 0
    assert second == 0
    total = len(list(await session.scalars(select(Request))))
    assert total == first


def test_seed_main_runs_without_bot_settings_and_is_idempotent(monkeypatch, tmp_path, capsys):
    import asyncio

    from sqlalchemy import create_engine, text

    from app.seed import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("ADMIN_IDS", raising=False)
    db_file = tmp_path / "demo.db"
    monkeypatch.setenv("DB_PATH", str(db_file))

    def count() -> int:
        engine = create_engine(f"sqlite:///{db_file}")
        try:
            with engine.connect() as conn:
                return conn.execute(text("select count(*) from requests")).scalar_one()
        finally:
            engine.dispose()

    asyncio.run(main())
    first = count()
    assert first >= 10
    asyncio.run(main())
    assert count() == first
    assert "уже есть" in capsys.readouterr().out
