from datetime import date, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Comment, Request, Role, Status, User
from app.db.repositories import comments as comments_repo
from app.db.repositories import requests as requests_repo
from app.services.validation import (
    validate_comment,
    validate_contact,
    validate_description,
    validate_name,
)
from app.timeutil import utcnow


class RequestError(Exception):
    pass


class RequestNotFound(RequestError):
    pass


class InvalidTransition(RequestError):
    pass


class EditNotAllowed(RequestError):
    pass


# Единственный источник правил переходов статусов.
ALLOWED_TRANSITIONS: dict[Status, set[Status]] = {
    Status.new: {Status.in_progress, Status.rejected},
    Status.in_progress: {Status.done, Status.rejected},
    Status.done: set(),
    Status.rejected: set(),
}

CLOSED_STATUSES = {Status.done, Status.rejected}
EDITABLE_FIELDS = {"client_name", "contact", "description", "preferred_contact_date"}


def _is_author(request: Request, actor: User) -> bool:
    return actor.role != Role.admin and request.user_id == actor.id


def can_edit(request: Request, actor: User) -> bool:
    """Единственное правило редактирования: автор-клиент и заявка в статусе «Новая»."""
    return _is_author(request, actor) and request.status == Status.new


def build_search_text(client_name: str, contact: str) -> str:
    return f"{client_name} {contact}".lower()


async def _get_fresh(session: AsyncSession, request_id: int) -> Request:
    """Актуальная строка из БД (а не устаревший объект из identity map)."""
    request = await session.scalar(
        select(Request)
        .where(Request.id == request_id)
        .execution_options(populate_existing=True)
    )
    if request is None:
        raise RequestNotFound(request_id)
    return request


async def create_request(
    session: AsyncSession,
    author: User,
    client_name: str,
    contact: str,
    description: str,
    preferred_contact_date: date,
    now: datetime | None = None,
) -> Request:
    now = now or utcnow()
    client_name = validate_name(client_name)
    contact = validate_contact(contact)
    description = validate_description(description)
    request = await requests_repo.add(
        session, author.id, client_name, contact, description, preferred_contact_date,
        build_search_text(client_name, contact), now,
    )
    await requests_repo.add_history(session, request.id, None, Status.new, author.id, now)
    await session.commit()
    return request


async def get_request_for_actor(session: AsyncSession, request_id: int, actor: User) -> Request:
    request = await _get_fresh(session, request_id)
    if actor.role != Role.admin and request.user_id != actor.id:
        raise RequestNotFound(request_id)
    return request


async def list_requests(
    session: AsyncSession,
    actor: User,
    *,
    status: Status | None = None,
    query: str | None = None,
    page: int = 0,
    page_size: int = 5,
) -> tuple[list[Request], int]:
    user_id = None if actor.role == Role.admin else actor.id
    return await requests_repo.list_page(
        session, user_id=user_id, status=status, query=query,
        offset=max(page, 0) * page_size, limit=page_size,
    )


async def edit_request(
    session: AsyncSession,
    request_id: int,
    actor: User,
    now: datetime | None = None,
    **fields,
) -> Request:
    unknown = set(fields) - EDITABLE_FIELDS
    if unknown:
        raise ValueError(f"Unknown fields: {sorted(unknown)}")
    now = now or utcnow()
    request = await _get_fresh(session, request_id)
    if not _is_author(request, actor):
        raise EditNotAllowed("Редактировать заявку может только её автор")
    if not can_edit(request, actor):
        raise EditNotAllowed("Заявку можно редактировать только в статусе «Новая»")

    validators = {
        "client_name": validate_name,
        "contact": validate_contact,
        "description": validate_description,
    }
    values = {
        key: validators[key](value) if key in validators else value
        for key, value in fields.items()
    }
    contact = values.get("contact", request.contact)
    client_name = values.get("client_name", request.client_name)
    result = await session.execute(
        update(Request)
        .where(Request.id == request_id, Request.status == Status.new)
        .values(**values, search_text=build_search_text(client_name, contact), updated_at=now)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        await session.rollback()
        if await session.get(Request, request_id) is None:
            raise RequestNotFound(request_id)
        raise EditNotAllowed("Заявку можно редактировать только в статусе «Новая»")
    await session.commit()
    return await _get_fresh(session, request_id)


async def change_status(
    session: AsyncSession,
    request_id: int,
    new_status: Status,
    admin: User,
    now: datetime | None = None,
) -> Request:
    now = now or utcnow()
    request = await _get_fresh(session, request_id)
    old_status = request.status
    if new_status not in ALLOWED_TRANSITIONS[old_status]:
        raise InvalidTransition(f"{old_status.value} -> {new_status.value}")
    changes = {"status": new_status, "updated_at": now}
    if new_status in CLOSED_STATUSES:
        changes["closed_at"] = now
    result = await session.execute(
        update(Request)
        .where(Request.id == request_id, Request.status == old_status)
        .values(**changes)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        await session.rollback()
        raise InvalidTransition(f"{old_status.value} -> {new_status.value}")
    await requests_repo.add_history(session, request_id, old_status, new_status, admin.id, now)
    await session.commit()
    return await _get_fresh(session, request_id)


async def add_comment(
    session: AsyncSession,
    request_id: int,
    admin: User,
    text: str,
    now: datetime | None = None,
) -> Comment:
    now = now or utcnow()
    text = validate_comment(text)
    request = await _get_fresh(session, request_id)
    comment = await comments_repo.add(session, request.id, admin.id, text, now)
    await session.commit()
    return comment


async def delete_request(session: AsyncSession, request_id: int) -> None:
    if not await requests_repo.delete(session, request_id):
        raise RequestNotFound(request_id)
    await session.commit()
