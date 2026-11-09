from datetime import date, datetime, timedelta

import pytest

from app.db.models import Role, Status
from app.db.repositories import users as users_repo
from app.services import analytics_service as analytics
from app.services import request_service
from app.services.analytics_service import compute, daily_summary_counts, period_bounds

TZ = "Europe/Moscow"
NOW = datetime(2026, 11, 5, 12, 0)


@pytest.fixture
async def people(session):
    author = await users_repo.get_or_create(session, 1, "client", "Client", Role.client)
    admin = await users_repo.get_or_create(session, 2, "admin", "Admin", Role.admin)
    await session.commit()
    return author, admin


async def make(session, people, created, *steps):
    """Создаёт заявку в момент created и применяет шаги (статус, момент)."""
    author, admin = people
    request = await request_service.create_request(
        session, author, "Иван", "+7 900 000-00-00", "Нужна консультация", date(2026, 11, 10), now=created
    )
    for status, at in steps:
        await request_service.change_status(session, request.id, status, admin, now=at)
    return request


def test_period_today_uses_local_day():
    now = datetime(2026, 11, 4, 21, 30)  # 00:30 МСК 5 ноября
    assert period_bounds("today", now, TZ) == (datetime(2026, 11, 4, 21, 0), None)


def test_period_all_is_unbounded():
    assert period_bounds("all", NOW, TZ) == (None, None)


def test_period_days_and_unknown():
    assert period_bounds("7d", NOW, TZ) == (NOW - timedelta(days=7), None)
    assert period_bounds("30d", NOW, TZ) == (NOW - timedelta(days=30), None)
    with pytest.raises(ValueError):
        period_bounds("year", NOW, TZ)


async def test_compute_counts_rates_and_avg(session, people):
    t = datetime(2026, 11, 1, 10, 0)
    await make(session, people, t, (Status.in_progress, t + timedelta(hours=1)),
               (Status.done, t + timedelta(hours=2)))
    await make(session, people, t, (Status.in_progress, t + timedelta(hours=1)),
               (Status.done, t + timedelta(hours=4)))
    await make(session, people, t, (Status.in_progress, t + timedelta(hours=1)))
    await make(session, people, t)

    stats = await compute(session, None, None)

    assert stats.total == 4
    assert stats.done == 2
    assert stats.to_in_progress_rate == 0.75
    assert stats.to_done_rate == pytest.approx(2 / 3)
    assert stats.avg_processing == timedelta(hours=3)


async def test_compute_empty_db_returns_none_not_error(session):
    stats = await compute(session, None, None)
    assert stats.total == 0
    assert stats.done == 0
    assert stats.to_in_progress_rate is None
    assert stats.to_done_rate is None
    assert stats.avg_processing is None


async def test_compute_respects_period(session, people):
    old = datetime(2026, 10, 1, 10, 0)
    new = datetime(2026, 11, 3, 10, 0)
    await make(session, people, old, (Status.in_progress, old + timedelta(hours=1)),
               (Status.done, old + timedelta(hours=2)))
    await make(session, people, new, (Status.in_progress, new + timedelta(hours=1)),
               (Status.done, new + timedelta(hours=6)))

    stats = await compute(session, datetime(2026, 11, 1), None)
    assert (stats.total, stats.done) == (1, 1)
    assert stats.avg_processing == timedelta(hours=6)

    # верхняя граница исключающая
    stats = await compute(session, None, new)
    assert (stats.total, stats.done) == (1, 1)
    assert stats.avg_processing == timedelta(hours=2)


async def test_compute_done_counted_by_closed_at(session, people):
    created = datetime(2026, 10, 1, 10, 0)
    closed = datetime(2026, 11, 3, 10, 0)
    await make(session, people, created, (Status.in_progress, created + timedelta(hours=1)),
               (Status.done, closed))

    stats = await compute(session, datetime(2026, 11, 1), None)
    assert stats.total == 0
    assert stats.done == 1
    assert stats.to_in_progress_rate is None
    assert stats.avg_processing == closed - created


async def test_daily_summary_counts(session, people):
    inp = (Status.in_progress, NOW - timedelta(hours=1))
    # новая за 24ч, не просроченная
    await make(session, people, NOW - timedelta(hours=2))
    # новая, старше порога 12ч и вне 24ч
    await make(session, people, NOW - timedelta(hours=30))
    # новая ровно на пороге 12ч (stale: <=), внутри 24ч
    await make(session, people, NOW - timedelta(hours=12))
    # в работе сейчас, создана давно
    await make(session, people, NOW - timedelta(days=3), inp)
    # завершена за 24ч
    await make(session, people, NOW - timedelta(days=3),
               (Status.in_progress, NOW - timedelta(hours=10)), (Status.done, NOW - timedelta(hours=5)))
    # завершена раньше 24ч назад
    await make(session, people, NOW - timedelta(days=3),
               (Status.in_progress, NOW - timedelta(days=2)), (Status.done, NOW - timedelta(hours=30)))

    counts = await daily_summary_counts(session, NOW, 12)

    assert counts == analytics.SummaryCounts(new_24h=2, in_progress=1, done_24h=1, stale_new=2)
