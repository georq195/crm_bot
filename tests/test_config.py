from datetime import datetime

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.timeutil import local_day_start_utc


def test_admin_ids_parsed_from_csv():
    assert Settings(bot_token="t", admin_ids="1, 2").admin_ids == [1, 2]


def test_empty_token_rejected():
    with pytest.raises(ValidationError):
        Settings(bot_token="", admin_ids="1")


def test_empty_admin_ids_rejected():
    with pytest.raises(ValidationError):
        Settings(bot_token="t", admin_ids="")


def test_local_day_start_utc():
    now = datetime(2026, 11, 4, 21, 30)
    assert local_day_start_utc(now, "Europe/Moscow") == datetime(2026, 11, 4, 21, 0)


@pytest.mark.parametrize("raw, expected", [("INFO", "INFO"), ("debug", "DEBUG"), (" warning ", "WARNING"),
                                           ("Error", "ERROR"), ("CRITICAL", "CRITICAL")])
def test_log_level_valid_and_normalized(raw, expected):
    assert Settings(bot_token="t", admin_ids="1", log_level=raw).log_level == expected


def test_log_level_default_is_info():
    assert Settings(bot_token="t", admin_ids="1").log_level == "INFO"


@pytest.mark.parametrize("bad", ["VERBOSE", "", "10", "NOTSET"])
def test_log_level_invalid_rejected(bad):
    with pytest.raises(ValidationError):
        Settings(bot_token="t", admin_ids="1", log_level=bad)
