from datetime import date, datetime
from enum import Enum

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.timeutil import utcnow


class Role(str, Enum):
    client = "client"
    admin = "admin"


class Status(str, Enum):
    new = "new"
    in_progress = "in_progress"
    done = "done"
    rejected = "rejected"


def _enum(cls):
    return SAEnum(cls, native_enum=False, length=20, values_callable=lambda e: [m.value for m in e])


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str] = mapped_column(String(256))
    role: Mapped[Role] = mapped_column(_enum(Role), default=Role.client)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Request(Base):
    __tablename__ = "requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    client_name: Mapped[str] = mapped_column(String(256))
    contact: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text)
    preferred_contact_date: Mapped[date] = mapped_column(Date)
    status: Mapped[Status] = mapped_column(_enum(Status), default=Status.new, index=True)
    search_text: Mapped[str] = mapped_column(Text, default="")
    last_reminded_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)

    author: Mapped[User] = relationship(lazy="joined")
    comments: Mapped[list["Comment"]] = relationship(
        lazy="selectin", order_by="Comment.id", passive_deletes=True,
        cascade="all, delete-orphan",
    )


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class StatusHistory(Base):
    __tablename__ = "status_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id", ondelete="CASCADE"), index=True)
    old_status: Mapped[Status | None] = mapped_column(_enum(Status))
    new_status: Mapped[Status] = mapped_column(_enum(Status))
    changed_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
