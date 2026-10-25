"""注册窗口与领取周期键的纯函数测试（无需数据库）"""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.system_settings_svc import (
    current_claim_period_key,
    current_register_window_start,
)


TZ = ZoneInfo("Asia/Shanghai")


def test_register_window_before_reset_hour() -> None:
    """重置前应归属前一天窗口"""
    now = datetime(2026, 7, 24, 5, 59, tzinfo=TZ)
    start = current_register_window_start(6, now)
    assert start == datetime(2026, 7, 23, 6, 0, tzinfo=TZ)


def test_register_window_after_reset_hour() -> None:
    """重置后应归属当天窗口"""
    now = datetime(2026, 7, 24, 6, 0, tzinfo=TZ)
    start = current_register_window_start(6, now)
    assert start == datetime(2026, 7, 24, 6, 0, tzinfo=TZ)


def test_claim_period_monthly() -> None:
    """月度领取键为 YYYY-MM"""
    now = datetime(2026, 7, 24, 10, 0, tzinfo=TZ)
    assert current_claim_period_key("monthly", 6, now) == "2026-07"


def test_claim_period_daily_before_reset() -> None:
    """日周期在重置小时前归属前一日"""
    now = datetime(2026, 7, 24, 5, 0, tzinfo=TZ)
    assert current_claim_period_key("daily", 6, now) == "2026-07-23"


def test_claim_period_daily_after_reset() -> None:
    """日周期在重置小时后归属当日"""
    now = datetime(2026, 7, 24, 6, 0, tzinfo=TZ)
    assert current_claim_period_key("daily", 6, now) == "2026-07-24"


def test_clamp_hour_out_of_range() -> None:
    """非法小时被钳制时窗口仍可计算"""
    now = datetime(2026, 7, 24, 12, 0, tzinfo=TZ)
    start = current_register_window_start(99, now)
    assert start.hour == 23
