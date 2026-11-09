import logging
from datetime import date, datetime, time, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import Settings
from app.db.models import Request, Role, Status, User
from app.db.repositories import requests as requests_repo
from app.scheduler import jobs
from app.services.analytics_service import SummaryCounts, daily_summary_counts
from app.services.notification_service import format_daily_summary, format_reminder
from app.timeutil import to_local

NOW = datetime(2026, 11, 5, 12, 0)
ADMINS = [100, 200]


class FalseSender:
    def __init__(self):
        self.calls = []

    async def send(self, tg_id, text, open_request_id=None) -> bool:
        self.calls.append((tg_id, text, open_request_id))
        return False


def make_settings(**overrides) -> Settings:
    values = dict(bot_token="token", admin_ids=ADMINS)
    values.update(overrides)
    return Settings(_env_file=None, **values)


async def add_request(session_factory, age: timedelta, status=Status.new, last_reminded_at=None) -> int:
    async with session_factory() as session:
        if await session.get(User, 1) is None:
            session.add(User(id=1, tg_id=1, username="u", full_name="Client", role=Role.client))
            await session.flush()
        request = await requests_repo.add(
            session, 1, "Иван", "+7 900 000-00-00", "Нужна консультация",
            date(2026, 11, 10), "иван", NOW - age,
        )
        request.status = status
        request.last_reminded_at = last_reminded_at
        await session.commit()
        return request.id


async def last_reminded(session_factory, request_id: int) -> datetime | None:
    async with session_factory() as session:
        return (await session.get(Request, request_id)).last_reminded_at


async def test_reminder_sent_for_old_new_request(session_factory, sender):
    request_id = await add_request(session_factory, timedelta(hours=5))
    settings = make_settings()

    count = await jobs.send_reminders(session_factory, sender, settings, now=NOW)

    assert count == 1
    assert [c[0] for c in sender.calls] == ADMINS
    assert all("#%d" % request_id in c[1] for c in sender.calls)
    assert await last_reminded(session_factory, request_id) == NOW


async def test_reminder_text_uses_format_reminder(session_factory, sender):
    await add_request(session_factory, timedelta(hours=5))
    settings = make_settings()
    async with session_factory() as session:
        due = await requests_repo.list_due_for_reminder(session, NOW - timedelta(hours=4), NOW - timedelta(hours=4))
        expected = format_reminder(due, settings.timezone)

    await jobs.send_reminders(session_factory, sender, settings, now=NOW)

    assert {c[1] for c in sender.calls} == {expected}


async def test_reminder_not_repeated_within_threshold(session_factory, sender):
    await add_request(session_factory, timedelta(hours=5))
    settings = make_settings()

    assert await jobs.send_reminders(session_factory, sender, settings, now=NOW) == 1
    assert len(sender.calls) == 2

    assert await jobs.send_reminders(session_factory, sender, settings, now=NOW) == 0
    assert len(sender.calls) == 2

    later = NOW + timedelta(hours=4, minutes=1)
    assert await jobs.send_reminders(session_factory, sender, settings, now=later) == 1
    assert len(sender.calls) == 4


async def test_reminder_skips_fresh_and_in_progress(session_factory, sender):
    await add_request(session_factory, timedelta(hours=1))
    in_progress = await add_request(session_factory, timedelta(hours=6), status=Status.in_progress)
    settings = make_settings()

    assert await jobs.send_reminders(session_factory, sender, settings, now=NOW) == 0
    assert sender.calls == []
    assert await last_reminded(session_factory, in_progress) is None


async def test_reminder_without_due_requests_sends_nothing(session_factory, sender):
    assert await jobs.send_reminders(session_factory, sender, make_settings(), now=NOW) == 0
    assert sender.calls == []


async def test_reminder_marks_all_due_requests_not_only_listed(session_factory, sender):
    ids = [await add_request(session_factory, timedelta(hours=5)) for _ in range(25)]

    count = await jobs.send_reminders(session_factory, sender, make_settings(), now=NOW)

    assert count == 25
    assert len(sender.calls) == 2
    for request_id in ids:
        assert await last_reminded(session_factory, request_id) == NOW


async def test_reminder_retried_when_every_send_returns_false(session_factory):
    request_id = await add_request(session_factory, timedelta(hours=5))
    settings = make_settings()
    false_sender = FalseSender()

    count = await jobs.send_reminders(session_factory, false_sender, settings, now=NOW)

    assert count == 1
    assert len(false_sender.calls) == 2
    assert await last_reminded(session_factory, request_id) is None


async def test_reminder_retried_when_every_send_raises(session_factory, failing_sender, sender):
    request_id = await add_request(session_factory, timedelta(hours=5))
    settings = make_settings()

    count = await jobs.send_reminders(session_factory, failing_sender, settings, now=NOW)

    assert count == 1
    assert await last_reminded(session_factory, request_id) is None

    # следующий интервал: отправка проходит, напоминание уходит
    assert await jobs.send_reminders(session_factory, sender, settings, now=NOW + timedelta(hours=1)) == 1
    assert len(sender.calls) == 2
    assert await last_reminded(session_factory, request_id) == NOW + timedelta(hours=1)


async def test_reminder_marked_when_at_least_one_admin_got_it(session_factory):
    request_id = await add_request(session_factory, timedelta(hours=5))

    class FirstFailsSender:
        def __init__(self):
            self.calls = []

        async def send(self, tg_id, text, open_request_id=None) -> bool:
            self.calls.append(tg_id)
            if tg_id == ADMINS[0]:
                raise RuntimeError("boom")
            return True

    partial = FirstFailsSender()
    assert await jobs.send_reminders(session_factory, partial, make_settings(), now=NOW) == 1
    assert partial.calls == ADMINS
    assert await last_reminded(session_factory, request_id) == NOW


async def test_daily_summary_sent_to_every_admin(session_factory, sender):
    await add_request(session_factory, timedelta(hours=5))
    await add_request(session_factory, timedelta(hours=1))
    settings = make_settings()
    async with session_factory() as session:
        counts = await daily_summary_counts(session, NOW, settings.reminder_after_hours)
    label = to_local(NOW, settings.timezone).strftime("%d.%m.%Y")
    assert label == "05.11.2026"

    await jobs.send_daily_summary(session_factory, sender, settings, now=NOW)

    assert counts == SummaryCounts(new_24h=2, in_progress=0, done_24h=0, stale_new=1)
    assert [c[0] for c in sender.calls] == ADMINS
    assert {c[1] for c in sender.calls} == {format_daily_summary(counts, label)}


async def test_daily_summary_date_label_is_local_date(session_factory, sender):
    late = datetime(2026, 11, 5, 22, 30)  # 01:30 следующего дня по Москве
    await jobs.send_daily_summary(session_factory, sender, make_settings(), now=late)
    assert "06.11.2026" in sender.calls[0][1]


async def test_daily_summary_failing_admin_does_not_block_others(session_factory, caplog):
    class FirstFailsSender:
        def __init__(self):
            self.calls = []

        async def send(self, tg_id, text, open_request_id=None) -> bool:
            if tg_id == ADMINS[0]:
                raise RuntimeError("boom")
            self.calls.append(tg_id)
            return True

    flaky = FirstFailsSender()
    with caplog.at_level(logging.ERROR):
        await jobs.send_daily_summary(session_factory, flaky, make_settings(), now=NOW)

    assert flaky.calls == [ADMINS[1]]
    assert any(r.levelno >= logging.ERROR for r in caplog.records)


def test_scheduler_registers_two_jobs(session_factory, sender):
    settings = make_settings(
        reminder_interval_min=15, daily_summary_time=time(7, 30), timezone="Asia/Yekaterinburg"
    )

    scheduler = jobs.build_scheduler(session_factory, sender, settings)

    assert isinstance(scheduler, AsyncIOScheduler)
    assert not scheduler.running
    assert str(scheduler.timezone) == "Asia/Yekaterinburg"
    assert sorted(job.id for job in scheduler.get_jobs()) == ["daily_summary", "reminders"]

    reminders = scheduler.get_job("reminders").trigger
    assert isinstance(reminders, IntervalTrigger)
    assert reminders.interval == timedelta(minutes=15)

    summary = scheduler.get_job("daily_summary").trigger
    assert isinstance(summary, CronTrigger)
    fields = {f.name: str(f) for f in summary.fields}
    assert fields["hour"] == "7"
    assert fields["minute"] == "30"
    assert str(summary.timezone) == "Asia/Yekaterinburg"


def test_scheduler_jobs_have_explicit_misfire_and_instance_options(session_factory, sender):
    settings = make_settings(reminder_interval_min=15, daily_summary_time=time(7, 30))

    scheduler = jobs.build_scheduler(session_factory, sender, settings)

    reminders = scheduler.get_job("reminders")
    summary = scheduler.get_job("daily_summary")
    assert (reminders.misfire_grace_time, reminders.coalesce, reminders.max_instances) == (900, True, 1)
    assert (summary.misfire_grace_time, summary.coalesce, summary.max_instances) == (3600, True, 1)


async def test_scheduled_jobs_call_job_functions(session_factory, sender):
    await add_request(session_factory, timedelta(hours=5))
    settings = make_settings()
    scheduler = jobs.build_scheduler(session_factory, sender, settings)

    await scheduler.get_job("daily_summary").func()

    assert [c[0] for c in sender.calls] == ADMINS


async def test_scheduled_job_exception_is_logged_not_raised(sender, caplog):
    def broken_factory():
        raise RuntimeError("db is gone")

    scheduler = jobs.build_scheduler(broken_factory, sender, make_settings())

    with caplog.at_level(logging.ERROR):
        for job_id in ("reminders", "daily_summary"):
            await scheduler.get_job(job_id).func()

    assert sender.calls == []
    assert len([r for r in caplog.records if r.levelno >= logging.ERROR]) == 2


async def test_reminder_does_not_bump_updated_at(session_factory, sender):
    request_id = await add_request(session_factory, timedelta(hours=10))
    async with session_factory() as session:
        before = (await session.get(Request, request_id)).updated_at

    await jobs.send_reminders(session_factory, sender, make_settings(), now=NOW)

    async with session_factory() as session:
        request = await session.get(Request, request_id)
        assert request.last_reminded_at == NOW
        assert request.updated_at == before
