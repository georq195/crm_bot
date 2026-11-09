from datetime import date, datetime, timedelta

from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage

from app.bot.callbacks import RequestCB
from app.bot.keyboards.menu import main_menu
from app.bot.middlewares import resolve_role
from app.bot.sender import TelegramSender
from app.bot.texts import format_request_card, format_stats, page_buttons
from app.db.models import Comment, Request, Role, Status
from app.services.analytics_service import Stats

TZ = "Europe/Moscow"


def make_request(description: str = "Нужна консультация") -> Request:
    return Request(
        id=7,
        user_id=1,
        client_name="Иван",
        contact="+79001234567",
        description=description,
        preferred_contact_date=date(2026, 11, 10),
        status=Status.in_progress,
        created_at=datetime(2026, 11, 4, 9, 0),
        updated_at=datetime(2026, 11, 4, 10, 30),
    )


def make_comment(i: int, text: str) -> Comment:
    return Comment(id=i, request_id=7, author_id=1, text=text, created_at=datetime(2026, 11, 4, 11, 0))


def test_resolve_role():
    assert resolve_role(5, [1, 5]) == Role.admin
    assert resolve_role(6, [1, 5]) == Role.client


def test_card_hides_comments_from_client():
    comments = [make_comment(1, "секретный комментарий")]
    client_card = format_request_card(make_request(), comments, for_admin=False, tz=TZ)
    admin_card = format_request_card(make_request(), comments, for_admin=True, tz=TZ)
    assert "секретный комментарий" not in client_card
    assert "секретный комментарий" in admin_card


def test_card_shows_local_dates_and_status_label():
    card = format_request_card(make_request(), [], for_admin=False, tz=TZ)
    assert "В работе" in card
    assert "04.11.2026 12:00" in card  # 09:00 UTC -> 12:00 MSK
    assert "04.11.2026 13:30" in card
    assert "10.11.2026" in card


def test_card_with_many_comments_fits_telegram_limit():
    comments = [make_comment(i, f"c{i:02d}-" + "я" * 296) for i in range(50)]
    card = format_request_card(make_request("д" * 1000), comments, for_admin=True, tz=TZ)
    assert len(card) <= 4096
    for i in range(40, 50):
        assert f"c{i:02d}-" in card
    for i in range(0, 40):
        assert f"c{i:02d}-" not in card


def test_card_truncates_description_as_last_resort():
    comments = [make_comment(i, f"c{i:02d}-" + "я" * 296) for i in range(10)]
    card = format_request_card(make_request("д" * 5000), comments, for_admin=True, tz=TZ)
    assert len(card) <= 4096
    assert "c09-" in card
    assert "…" in card


def test_card_with_html_like_text_is_unchanged():
    req = make_request("<b>x</b> & <i>y</i>")
    req.client_name = "<b>Иван</b>"
    card = format_request_card(req, [make_comment(1, "<code>z</code>")], for_admin=True, tz=TZ)
    for fragment in ("<b>x</b> & <i>y</i>", "<b>Иван</b>", "<code>z</code>"):
        assert fragment in card


def test_format_stats():
    stats = Stats(total=10, done=4, to_in_progress_rate=0.5, to_done_rate=0.4, avg_processing=timedelta(hours=2, minutes=30))
    text = format_stats(stats, "7 дней")
    assert "7 дней" in text
    assert "10" in text and "50%" in text and "40%" in text
    assert "2 ч 30 мин" in text


def test_format_stats_without_data():
    stats = Stats(total=0, done=0, to_in_progress_rate=None, to_done_rate=None, avg_processing=None)
    assert "нет данных" in format_stats(stats, "сегодня")


def test_page_buttons():
    assert page_buttons(0, 0) == []
    assert page_buttons(0, 5) == []  # одна страница
    assert page_buttons(0, 12) == [("›", 1)]  # первая
    assert page_buttons(1, 12) == [("‹", 0), ("›", 2)]  # средняя
    assert page_buttons(2, 12) == [("‹", 1)]  # последняя (12 записей -> 3 страницы)
    assert page_buttons(0, 6) == [("›", 1)]


def test_main_menu_by_role():
    def labels(role):
        return [b.text for row in main_menu(role).keyboard for b in row]

    assert labels(Role.client) == ["Новая заявка", "Мои заявки"]
    assert sorted(labels(Role.admin)) == sorted(["Заявки", "Поиск", "Аналитика", "Экспорт", "Новая заявка"])


class FakeBot:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.sent: list[dict] = []

    async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
        if self.error:
            raise self.error
        self.sent.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup, **kwargs})


async def test_telegram_sender_returns_false_on_forbidden(caplog):
    error = TelegramForbiddenError(method=SendMessage(chat_id=1, text="x"), message="bot was blocked")
    sender = TelegramSender(FakeBot(error))
    assert await sender.send(1, "привет") is False
    assert any(r.levelname in ("WARNING", "ERROR") for r in caplog.records)


async def test_telegram_sender_adds_open_button_and_no_parse_mode():
    bot = FakeBot()
    assert await TelegramSender(bot).send(5, "<b>x</b>", open_request_id=42) is True
    sent = bot.sent[0]
    assert sent["chat_id"] == 5 and sent["text"] == "<b>x</b>"
    assert "parse_mode" not in sent
    button = sent["reply_markup"].inline_keyboard[0][0]
    assert button.text == "Открыть"
    assert button.callback_data == RequestCB(action="open", id=42).pack()


async def test_telegram_sender_without_request_has_no_markup():
    bot = FakeBot()
    assert await TelegramSender(bot).send(5, "текст") is True
    assert bot.sent[0]["reply_markup"] is None


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def test_fit_message_leaves_short_and_exact_limit_text_unchanged():
    from app.bot.texts import fit_message

    assert fit_message("привет") == "привет"
    exact = "a" * 4096
    assert fit_message(exact) == exact
    exact_emoji = "😀" * 2048  # ровно 4096 единиц UTF-16
    assert fit_message(exact_emoji) == exact_emoji


def test_fit_message_truncates_ascii_with_ellipsis():
    from app.bot.texts import fit_message

    result = fit_message("a" * 5000)
    assert utf16_len(result) <= 4096
    assert result == "a" * 4095 + "…"


def test_fit_message_truncates_cyrillic():
    from app.bot.texts import fit_message

    result = fit_message("ж" * 4097)
    assert result == "ж" * 4095 + "…"


def test_fit_message_counts_emoji_as_two_units_and_keeps_pairs_whole():
    from app.bot.texts import fit_message

    result = fit_message("😀" * 3000)
    assert utf16_len(result) <= 4096
    assert result.endswith("…")
    body = result[:-1]
    assert body == "😀" * len(body)  # ни одной половинки суррогатной пары
    result.encode("utf-16-le")  # не падает на «одиноком» суррогате
    # после нечётной границы: 1 ASCII + эмодзи, так что 4095-я единица попадает внутрь пары
    shifted = fit_message("a" + "😀" * 3000)
    shifted.encode("utf-16-le")
    assert utf16_len(shifted) <= 4096 and shifted.endswith("…")


async def test_telegram_sender_caps_oversized_text():
    bot = FakeBot()
    await TelegramSender(bot).send(5, "😀" * 3000)
    assert utf16_len(bot.sent[0]["text"]) <= 4096


async def test_edit_or_answer_caps_text_for_edit_and_fallback():
    from aiogram.exceptions import TelegramBadRequest

    from app.bot.routers.client.helpers import edit_or_answer

    class Target:
        def __init__(self, fail):
            self.fail, self.texts = fail, []

        async def edit_text(self, text, reply_markup=None):
            if self.fail:
                raise TelegramBadRequest(method=SendMessage(chat_id=1, text="x"), message="boom")
            self.texts.append(text)

        async def answer(self, text, reply_markup=None):
            self.texts.append(text)

    for fail in (False, True):
        target = Target(fail)
        await edit_or_answer(target, "😀" * 3000)
        assert len(target.texts) == 1 and utf16_len(target.texts[0]) <= 4096


def _marked(markup) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row if b.text.startswith("•")]


def test_status_filter_kb_marks_active_filter_only():
    from app.bot.keyboards.requests import status_filter_kb

    assert _marked(status_filter_kb(None)) == []
    assert _marked(status_filter_kb("")) == ["• Все"]
    assert _marked(status_filter_kb("new")) == ["• Новые"]
