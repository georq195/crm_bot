import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from pydantic import ValidationError

from app.bot.errors import on_error
from app.bot.middlewares import DbSessionMiddleware, UserMiddleware
from app.bot.routers import common, fallback
from app.bot.routers.admin import analytics, export, request_card, requests_list
from app.bot.routers.client import create_request, edit_request, my_requests
from app.bot.sender import TelegramSender
from app.config import Settings, get_settings
from app.db.base import init_db, make_engine, make_session_factory
from app.scheduler.jobs import build_scheduler

logger = logging.getLogger(__name__)


def build_dispatcher(session_factory, settings: Settings) -> Dispatcher:
    dp = Dispatcher()
    dp["settings"] = settings

    for observer in (dp.message, dp.callback_query):
        observer.outer_middleware(DbSessionMiddleware(session_factory))
        observer.outer_middleware(UserMiddleware(settings.admin_ids))

    dp.errors.register(on_error)

    # common первым, чтобы «Отмена» работала в любом состоянии
    dp.include_router(common.build_router())
    dp.include_router(create_request.build_router())
    dp.include_router(my_requests.build_router())
    dp.include_router(edit_request.build_router())
    dp.include_router(requests_list.build_router())
    dp.include_router(request_card.build_router())
    dp.include_router(analytics.build_router())
    dp.include_router(export.build_router())
    # fallback последним
    dp.include_router(fallback.build_router())
    return dp


def describe_settings_error(error: ValidationError) -> str:
    lines = []
    for item in error.errors():
        name = ".".join(str(p) for p in item["loc"]).upper() or "НАСТРОЙКИ"
        if item["type"] == "missing":
            lines.append(f"{name} не задан")
            continue
        msg = item["msg"].removeprefix("Value error, ")
        lines.append(msg if name in msg else f"{name}: некорректное значение ({msg})")
    return "\n".join(f"  - {line}" for line in lines)


def load_settings_or_exit() -> Settings:
    try:
        return get_settings()
    except ValidationError as exc:
        print(
            "Бот не запущен: проверьте настройки (файл .env или переменные окружения).\n"
            f"{describe_settings_error(exc)}",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


async def main() -> None:
    settings = load_settings_or_exit()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    engine = make_engine(settings.db_path)
    await init_db(engine)
    session_factory = make_session_factory(engine)

    bot = Bot(settings.bot_token)
    dp = build_dispatcher(session_factory, settings)
    scheduler = build_scheduler(session_factory, TelegramSender(bot), settings)
    scheduler.start()
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
