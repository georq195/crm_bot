from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    """Текущее время UTC без tzinfo (так даты хранятся в БД)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_local(dt: datetime, tz: str) -> datetime:
    """Naive UTC -> naive локальное время в часовом поясе tz."""
    return dt.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(tz)).replace(tzinfo=None)


def local_day_start_utc(now: datetime, tz: str) -> datetime:
    """Начало текущих локальных суток для момента now (naive UTC), в naive UTC."""
    local = to_local(now, tz)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=ZoneInfo(tz))
    return start.astimezone(timezone.utc).replace(tzinfo=None)
