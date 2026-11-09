from datetime import time
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class DbSettings(BaseSettings):
    """Только путь к базе: для скриптов вроде app.seed, которым не нужен токен бота."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    db_path: str = "data/crm.db"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    admin_ids: Annotated[list[int], NoDecode]
    db_path: str = "data/crm.db"
    timezone: str = "Europe/Moscow"
    reminder_interval_min: int = 60
    reminder_after_hours: int = 4
    daily_summary_time: time = time(9, 0)
    log_level: str = "INFO"

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, value):
        if isinstance(value, str):
            return [int(p) for p in value.split(",") if p.strip()]
        return value

    @field_validator("bot_token")
    @classmethod
    def _token_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("BOT_TOKEN не задан")
        return value.strip()

    @field_validator("log_level")
    @classmethod
    def _log_level_known(cls, value: str) -> str:
        level = value.strip().upper()
        if level not in _LOG_LEVELS:
            raise ValueError(f"LOG_LEVEL: допустимы значения {', '.join(_LOG_LEVELS)}")
        return level

    @field_validator("admin_ids")
    @classmethod
    def _admins_not_empty(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("ADMIN_IDS не задан")
        return value


def get_settings() -> Settings:
    return Settings()
