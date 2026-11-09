import logging
from datetime import date, datetime

from app.db.models import Request, Role, Status, User
from app.services import notification_service as ns
from app.services.analytics_service import SummaryCounts

TZ = "Europe/Moscow"


def make_user(tg_id=1):
    return User(id=tg_id, tg_id=tg_id, username="u", full_name="Client", role=Role.client)


def make_request(i=1, name="Иван", contact="+7 900 000-00-00", description="Нужна консультация"):
    return Request(
        id=i, user_id=1, client_name=name, contact=contact, description=description,
        preferred_contact_date=date(2026, 11, 10), status=Status.new,
        created_at=datetime(2026, 11, 5, 12, 0),
    )


async def test_new_request_goes_to_admins_except_author(sender):
    author = make_user(2)
    request = make_request(7)
    await ns.notify_new_request(sender, request, author, [1, 2, 3], TZ)
    assert [c[0] for c in sender.calls] == [1, 3]
    assert all(c[2] == 7 for c in sender.calls)
    assert "Иван" in sender.calls[0][1]


async def test_status_change_goes_to_author_with_label(sender):
    author = make_user(5)
    await ns.notify_status_changed(sender, make_request(7), author, Status.in_progress, changed_by_tg_id=99)
    assert len(sender.calls) == 1
    tg_id, text, open_id = sender.calls[0]
    assert tg_id == 5
    assert "В работе" in text
    assert open_id == 7


async def test_status_change_skips_self(sender):
    author = make_user(5)
    await ns.notify_status_changed(sender, make_request(), author, Status.done, changed_by_tg_id=5)
    assert sender.calls == []


async def test_sender_exception_does_not_propagate(failing_sender, caplog):
    author = make_user(5)
    with caplog.at_level(logging.ERROR, logger=ns.__name__):
        await ns.notify_new_request(failing_sender, make_request(), author, [1, 2], TZ)
        await ns.notify_status_changed(failing_sender, make_request(), author, Status.done, 99)
    assert any(r.name == ns.__name__ for r in caplog.records)


async def test_failure_for_one_admin_does_not_stop_others():
    calls = []

    class Flaky:
        async def send(self, tg_id, text, open_request_id=None):
            if tg_id == 1:
                raise RuntimeError("boom")
            calls.append(tg_id)
            return True

    await ns.notify_new_request(Flaky(), make_request(), make_user(5), [1, 2, 3], TZ)
    assert calls == [2, 3]


def test_brief_truncates_description_and_uses_local_tz():
    text = ns.format_request_brief(make_request(description="я" * 200), TZ)
    assert "я" * 79 + "…" in text
    assert "я" * 80 not in text
    assert "05.11.2026 15:00" in text  # 12:00 UTC -> 15:00 МСК
    assert "10.11.2026" in text


def test_format_reminder_truncates_long_list():
    requests = [make_request(i) for i in range(1, 201)]
    text = ns.format_reminder(requests, TZ)
    assert len(text) <= 4096
    assert text.endswith("и ещё 180")
    assert text.count("#") == ns.MAX_LISTED


def test_format_reminder_worst_case_keeps_tail_within_limit():
    requests = [
        make_request(i, name="Я" * 100, contact="9" * 32, description="д" * 500)
        for i in range(100000, 100200)
    ]
    text = ns.format_reminder(requests, TZ)
    assert len(text) <= 4096
    shown = text.count("#")
    assert shown >= 1
    assert text.endswith(f"и ещё {200 - shown}")


def test_format_reminder_tail_counts_blocks_dropped_by_length_budget():
    requests = [
        make_request(i, name="Я" * 100, contact="9" * 32, description="д" * 500)
        for i in range(100000, 100020)
    ]
    text = ns.format_reminder(requests, TZ)
    assert len(text) <= 4096
    shown = text.count("#")
    assert 1 <= shown < 20
    assert text.endswith(f"и ещё {20 - shown}")
    assert text.split("\n\n")[-2].endswith("связаться: 10.11.2026")  # блок не обрезан


def test_format_reminder_single_oversized_block_is_truncated():
    big = make_request(1, name="Я" * 100, contact="9" * 32, description="д" * 500)
    big.client_name = "Я" * 5000  # заведомо больше бюджета
    text = ns.format_reminder([big, make_request(2)], TZ)
    assert len(text) <= 4096
    assert "#1 " in text and "…" in text
    assert text.endswith("и ещё 1")


def test_format_reminder_short_list_has_no_tail():
    text = ns.format_reminder([make_request(1), make_request(2)], TZ)
    assert "и ещё" not in text
    assert "#1" in text and "#2" in text


def test_format_daily_summary_contains_all_counts():
    text = ns.format_daily_summary(
        SummaryCounts(new_24h=11, in_progress=22, done_24h=33, stale_new=44), "05.11.2026"
    )
    for n in ("11", "22", "33", "44", "05.11.2026"):
        assert n in text
