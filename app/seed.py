"""Демо-данные: python -m app.seed (локально) или docker compose run --rm bot python -m app.seed."""
import asyncio
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import DbSettings
from app.db.base import init_db, make_engine, make_session_factory
from app.db.models import Request, Role, Status
from app.db.repositories import users as users_repo
from app.services import request_service
from app.timeutil import utcnow

# Демо-пользователи в заведомо «чужом» диапазоне id, чтобы не пересечься с реальными.
DEMO_CLIENTS = [(900000001, "demo_anna", "Анна Демо"), (900000002, "demo_ivan", "Иван Демо")]
DEMO_ADMIN = (900000003, "demo_admin", "Админ Демо")

# (клиент, имя, контакт, описание, дней назад, [(новый статус, через часов)], [(комментарий, через часов)])
_REQUESTS = [
    (0, "Анна Петрова", "+79991234567", "Нужен чат-бот для записи клиентов в салон красоты", 10,
     [(Status.in_progress, 2), (Status.done, 30)], [("Созвонились, ТЗ согласовано", 5)]),
    (1, "Иван Смирнов", "@ivan_smirnov", "Автоматизировать выгрузку отчётов из Excel", 9,
     [(Status.in_progress, 3), (Status.done, 48)], [("Отчёты настроены, клиент доволен", 50)]),
    (0, "ООО «Ромашка»", "+74951234567", "Нужна интеграция заявок с CRM", 8,
     [(Status.rejected, 5)], [("Не наш профиль, рекомендовали подрядчика", 4)]),
    (1, "Мария Козлова", "@maria_k", "Бот-напоминалка для клиентов стоматологии", 7,
     [(Status.in_progress, 6)], [("Уточняем объём рассылок", 8)]),
    (0, "Пётр Сидоров", "+79261112233", "Парсинг цен конкурентов в таблицу", 6,
     [(Status.in_progress, 1), (Status.done, 20)], []),
    (1, "Ольга Новикова", "@olga_nov", "Телеграм-бот для приёма заказов в кофейне", 5,
     [(Status.in_progress, 4)], [("Ждём меню от заказчика", 10), ("Меню получено, начали работу", 30)]),
    (0, "Дмитрий Орлов", "+79037778899", "Автоматизация создания договоров из шаблона", 4,
     [(Status.rejected, 12)], []),
    (1, "Елена Волкова", "@elena_volkova", "Нужен экспорт заявок в Excel по расписанию", 3,
     [(Status.in_progress, 5)], []),
    (0, "Сергей Лебедев", "+79150001122", "Бот для сбора обратной связи после покупки", 2,
     [], []),
    (1, "Наталья Морозова", "@natalia_m", "Сводный отчёт по продажам из нескольких CSV", 1,
     [], []),
    (0, "Алексей Фёдоров", "+79054445566", "Нужен бот для учёта заявок на ремонт", 0.5,
     [], []),
    (1, "Ирина Кузнецова", "@irina_kuz", "Уведомления о новых заказах в Telegram", 0.2,
     [], []),
]


async def seed(session: AsyncSession) -> int:
    """Создаёт демо-пользователей и заявки через сервисы; возвращает число заявок."""
    now = utcnow()
    clients = [
        await users_repo.get_or_create(session, tg_id, username, name, Role.client)
        for tg_id, username, name in DEMO_CLIENTS
    ]
    admin = await users_repo.get_or_create(session, DEMO_ADMIN[0], DEMO_ADMIN[1], DEMO_ADMIN[2], Role.admin)
    await session.commit()

    for client_idx, name, contact, description, days_ago, steps, comments in _REQUESTS:
        created = now - timedelta(days=days_ago)
        request = await request_service.create_request(
            session, clients[client_idx], name, contact, description,
            (created + timedelta(days=3)).date(), now=created,
        )
        moment = created
        for new_status, after_hours in steps:
            moment = moment + timedelta(hours=after_hours)
            await request_service.change_status(session, request.id, new_status, admin, now=moment)
        for text, after_hours in comments:
            await request_service.add_comment(
                session, request.id, admin, text, now=created + timedelta(hours=after_hours)
            )
    return len(_REQUESTS)


async def seed_if_empty(session: AsyncSession) -> int:
    """Не дублирует демо-данные при повторном запуске: если заявки уже есть, ничего не делает."""
    existing = await session.scalar(select(func.count()).select_from(Request))
    if existing:
        return 0
    return await seed(session)


async def main() -> None:
    settings = DbSettings()
    engine = make_engine(settings.db_path)
    await init_db(engine)
    try:
        async with make_session_factory(engine)() as session:
            created = await seed_if_empty(session)
    finally:
        await engine.dispose()
    if created:
        print(f"Демо-данные добавлены: {created} заявок (база {settings.db_path}).")
    else:
        print("В базе уже есть заявки, демо-данные не добавлены.")


if __name__ == "__main__":
    asyncio.run(main())
