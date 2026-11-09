import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import update

from app.config import Settings
from app.db.models import Request
from app.db.repositories import requests as requests_repo
from app.services.analytics_service import daily_summary_counts
from app.services.notification_service import Sender, format_daily_summary, format_reminder
from app.timeutil import to_local, utcnow

logger = logging.getLogger(__name__)


async def send_reminders(
    session_factory, sender: Sender, settings: Settings, now: datetime | None = None
) -> int:
    """Одно сообщение каждому админу со списком необработанных заявок.

    Возвращает число заявок в напоминании. last_reminded_at ставится, только если
    хотя бы одна отправка удалась, иначе напоминание повторится в следующий запуск.
    """
    now = now or utcnow()
    threshold = now - timedelta(hours=settings.reminder_after_hours)
    async with session_factory() as session:
        due = await requests_repo.list_due_for_reminder(session, threshold, threshold)
        if not due:
            return 0

        text = format_reminder(due, settings.timezone)
        delivered = False
        for admin_id in settings.admin_ids:
            try:
                delivered = await sender.send(admin_id, text) or delivered
            except Exception:
                logger.exception("Не удалось отправить напоминание админу %s", admin_id)

        if delivered:
            # через core update, чтобы не трогать updated_at
            await session.execute(
                update(Request)
                .where(Request.id.in_([request.id for request in due]))
                .values(last_reminded_at=now, updated_at=Request.updated_at)
            )
            await session.commit()
        return len(due)


async def send_daily_summary(
    session_factory, sender: Sender, settings: Settings, now: datetime | None = None
) -> None:
    now = now or utcnow()
    async with session_factory() as session:
        counts = await daily_summary_counts(session, now, settings.reminder_after_hours)

    date_label = to_local(now, settings.timezone).strftime("%d.%m.%Y")
    text = format_daily_summary(counts, date_label)
    for admin_id in settings.admin_ids:
        try:
            await sender.send(admin_id, text)
        except Exception:
            logger.exception("Не удалось отправить сводку админу %s", admin_id)


def build_scheduler(session_factory, sender: Sender, settings: Settings) -> AsyncIOScheduler:
    """Планировщик с заданиями reminders и daily_summary; не запущен."""

    async def reminders_job() -> None:
        try:
            await send_reminders(session_factory, sender, settings)
        except Exception:
            logger.exception("Сбой задания напоминаний")

    async def daily_summary_job() -> None:
        try:
            await send_daily_summary(session_factory, sender, settings)
        except Exception:
            logger.exception("Сбой задания дневной сводки")

    scheduler = AsyncIOScheduler(timezone=settings.timezone)
    scheduler.add_job(
        reminders_job,
        IntervalTrigger(minutes=settings.reminder_interval_min, timezone=settings.timezone),
        id="reminders",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=settings.reminder_interval_min * 60,
    )
    scheduler.add_job(
        daily_summary_job,
        CronTrigger(
            hour=settings.daily_summary_time.hour,
            minute=settings.daily_summary_time.minute,
            timezone=settings.timezone,
        ),
        id="daily_summary",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    return scheduler
