"""get_trading_day 单元测试

覆盖：日盘、夜盘、周五夜盘→周一、周末跳过、跨年。
"""

from datetime import datetime, timezone, timedelta

from app.services.trading_hours import get_trading_day

_BJ_TZ = timezone(timedelta(hours=8))


def _bj(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=_BJ_TZ)


def test_daytime_10am() -> None:
    """白天 10:00 → 当日"""
    assert get_trading_day(_bj(2026, 7, 22, 10, 0)) == _bj(2026, 7, 22, 0, 0)


def test_afternoon_close_3pm() -> None:
    assert get_trading_day(_bj(2026, 7, 22, 15, 0)) == _bj(2026, 7, 22, 0, 0)


def test_before_night_2059() -> None:
    assert get_trading_day(_bj(2026, 7, 22, 20, 59)) == _bj(2026, 7, 22, 0, 0)


def test_weeknight_open_21_to_next_day() -> None:
    """周三夜盘 21:00 → 周四"""
    assert get_trading_day(_bj(2026, 7, 22, 21, 0)) == _bj(2026, 7, 23, 0, 0)
    assert get_trading_day(_bj(2026, 7, 22, 22, 30)) == _bj(2026, 7, 23, 0, 0)


def test_thursday_night_to_friday() -> None:
    assert get_trading_day(_bj(2026, 7, 23, 21, 0)) == _bj(2026, 7, 24, 0, 0)


def test_friday_night_to_monday() -> None:
    """周五夜盘归属下周一，不出现周六日"""
    assert get_trading_day(_bj(2026, 7, 24, 21, 0)) == _bj(2026, 7, 27, 0, 0)
    assert get_trading_day(_bj(2026, 7, 24, 22, 30)) == _bj(2026, 7, 27, 0, 0)
    assert get_trading_day(_bj(2026, 7, 24, 23, 59)) == _bj(2026, 7, 27, 0, 0)


def test_saturday_early_from_friday_night_to_monday() -> None:
    """周六凌晨（贵金属跨夜）仍属周一"""
    assert get_trading_day(_bj(2026, 7, 25, 1, 0)) == _bj(2026, 7, 27, 0, 0)
    assert get_trading_day(_bj(2026, 7, 25, 2, 30)) == _bj(2026, 7, 27, 0, 0)


def test_saturday_daytime_skips_to_monday() -> None:
    assert get_trading_day(_bj(2026, 7, 25, 10, 0)) == _bj(2026, 7, 27, 0, 0)


def test_sunday_daytime_to_monday() -> None:
    assert get_trading_day(_bj(2026, 7, 26, 10, 0)) == _bj(2026, 7, 27, 0, 0)


def test_sunday_night_to_monday() -> None:
    """周日 21:00 夜盘 → 周一"""
    assert get_trading_day(_bj(2026, 7, 26, 21, 0)) == _bj(2026, 7, 27, 0, 0)


def test_monday_day() -> None:
    assert get_trading_day(_bj(2026, 7, 27, 10, 0)) == _bj(2026, 7, 27, 0, 0)


def test_monday_night_to_tuesday() -> None:
    assert get_trading_day(_bj(2026, 7, 27, 21, 0)) == _bj(2026, 7, 28, 0, 0)


def test_utc_friday_night() -> None:
    """UTC 13:00 = 北京周五 21:00 → 周一"""
    utc_now = datetime(2026, 7, 24, 13, 0, tzinfo=timezone.utc)
    assert get_trading_day(utc_now) == _bj(2026, 7, 27, 0, 0)


def test_year_boundary_weekday() -> None:
    """周四 12/31 21:00 → 次年 1/1（若为周五）需按实际 weekday
    2026-12-31 是周四，夜盘 → 2027-01-01 周五
    """
    assert get_trading_day(_bj(2026, 12, 31, 21, 0)) == _bj(2027, 1, 1, 0, 0)
