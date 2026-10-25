"""交易时段配置：黑色/橡胶夜盘 23:00 收盘，不应配到 02:30"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from app.services.session_profiles import get_profile_key
from app.services.trading_hours import get_session_status

_BJ = timezone(timedelta(hours=8))


def test_black_and_rubber_night_to_2300() -> None:
    for code in ("RB", "HC", "I", "J", "JM", "RU", "NR", "BU", "FU"):
        assert get_profile_key(code) == "commodity_n2300", code


def test_precious_still_0230() -> None:
    for code in ("AU", "AG", "SC"):
        assert get_profile_key(code) == "commodity_n0230", code


def test_closed_after_2300_for_hc_ru() -> None:
    # 工作日 23:26 应休市（夜盘 23:00 结束）
    now = datetime(2026, 7, 23, 23, 26, tzinfo=_BJ)
    for symbol in ("hc2610", "ru2609", "rb2610"):
        st = get_session_status(symbol, now)
        assert st["is_open"] is False, symbol


def test_open_before_2300_for_hc() -> None:
    now = datetime(2026, 7, 23, 22, 30, tzinfo=_BJ)
    st = get_session_status("hc2610", now)
    assert st["is_open"] is True
