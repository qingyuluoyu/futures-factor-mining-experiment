"""交易日历守卫回归 —— 周日/周一凌晨幽灵 K 线

2026-08-23（周日）事故：夜盘判定把「周日 21:00 起」当作夜盘开盘、
「周一凌晨」当作跨午夜尾巴开盘。数据源（vvtr 中继）周末照推报价，
聚合逻辑产出周日 21:00-23:00 的幽灵分钟 K 并固化进历史缓存，
图表出现非交易日 K 线（pp2701 实测）。

修复后：夜盘只存在于周一~周五晚间；周五尾巴到周六凌晨；
周日全天、周一凌晨一律休市。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.services import kline
from app.services.kline import _is_minute_bar_in_session, update_realtime_klines
from app.services.trading_hours import (
    _COARSE_SESSIONS,
    _is_weekend_closed,
    get_session_status,
)

_BJ = timezone(timedelta(hours=8))

# 2026-08：21=周五 22=周六 23=周日 24=周一 25=周二


def _dt(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=_BJ)


# ---------- _is_weekend_closed ----------


def test_sunday_night_is_closed() -> None:
    """周日晚 21:00-23:00 不是夜盘（修复前放行 → 幽灵 K 直接根因）。"""
    assert _is_weekend_closed(_dt(23, 22), _COARSE_SESSIONS) is True
    assert _is_weekend_closed(_dt(23, 21), _COARSE_SESSIONS) is True


def test_sunday_daytime_is_closed() -> None:
    assert _is_weekend_closed(_dt(23, 10), _COARSE_SESSIONS) is True


def test_monday_early_morning_is_closed() -> None:
    """周一凌晨没有跨午夜尾巴（周日晚无夜盘；周五尾巴最晚到周六凌晨）。"""
    assert _is_weekend_closed(_dt(24, 0, 17), _COARSE_SESSIONS) is True
    assert _is_weekend_closed(_dt(24, 1, 30), _COARSE_SESSIONS) is True


def test_saturday_early_morning_open_for_cross_midnight() -> None:
    """周六凌晨是周五夜盘延续（跨午夜 session 尾巴），可开。"""
    assert _is_weekend_closed(_dt(22, 1, 0), _COARSE_SESSIONS) is False


def test_saturday_daytime_and_evening_closed() -> None:
    assert _is_weekend_closed(_dt(22, 10), _COARSE_SESSIONS) is True
    assert _is_weekend_closed(_dt(22, 21, 30), _COARSE_SESSIONS) is True


def test_weekday_night_and_tuesday_tails_open() -> None:
    assert _is_weekend_closed(_dt(21, 22), _COARSE_SESSIONS) is False  # 周五夜盘
    assert _is_weekend_closed(_dt(24, 21, 30), _COARSE_SESSIONS) is False  # 周一夜盘
    assert _is_weekend_closed(_dt(25, 0, 30), _COARSE_SESSIONS) is False  # 周一夜盘尾巴


# ---------- get_session_status（品种级） ----------


def test_pp_sunday_night_closed() -> None:
    status = get_session_status("pp2701", _dt(23, 22))
    assert status["is_open"] is False


def test_au_saturday_early_morning_open() -> None:
    """au 夜盘到 02:30：周六 01:00 仍在周五夜盘尾巴内。"""
    status = get_session_status("au2612", _dt(22, 1, 0))
    assert status["is_open"] is True


def test_au_monday_early_morning_closed() -> None:
    """au 虽有跨午夜夜盘，但周一凌晨无前置交易晚间。"""
    status = get_session_status("au2612", _dt(24, 0, 30))
    assert status["is_open"] is False


def test_rb_monday_night_open() -> None:
    status = get_session_status("rb2610", _dt(24, 21, 30))
    assert status["is_open"] is True


# ---------- _is_minute_bar_in_session（写路径双保险 + 读路径 forming 门） ----------


@pytest.mark.parametrize(
    "time_str,expected",
    [
        ("2026-08-23 22:44:00", False),  # 周日夜（修复前 True）
        ("2026-08-23 10:00:00", False),  # 周日白天
        ("2026-08-24 00:17:00", False),  # 周一凌晨（修复前 True）
        ("2026-08-24 01:30:00", False),  # 周一凌晨
        ("2026-08-22 01:00:00", True),  # 周六凌晨 = 周五尾巴
        ("2026-08-22 10:00:00", False),  # 周六日盘
        ("2026-08-22 21:30:00", False),  # 周六晚
        ("2026-08-21 22:00:00", True),  # 周五夜盘
        ("2026-08-24 21:30:00", True),  # 周一夜盘
        ("2026-08-25 00:30:00", True),  # 周二凌晨 = 周一尾巴
        ("2026-08-24 10:00:00", True),  # 周一日盘
        ("2026-08-24 12:00:00", False),  # 午休
        ("2026-08-24 16:00:00", False),  # 日盘后
    ],
)
def test_minute_bar_session_calendar(time_str: str, expected: bool) -> None:
    assert _is_minute_bar_in_session(time_str) is expected


# ---------- update_realtime_klines：休市报价不产任何幽灵 bar ----------


class _FakeRedis:
    def __init__(self, store: dict[str, str] | None = None) -> None:
        self.store: dict[str, str] = dict(store or {})

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


class _SundayClock(datetime):
    """固定在 2026-08-23（周日）22:30 北京时间的时钟。"""

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        base = datetime(2026, 8, 23, 22, 30, tzinfo=_BJ)
        return base.astimezone(tz) if tz else base.replace(tzinfo=None)


def _real_calendar_on_sunday(monkeypatch: pytest.MonkeyPatch) -> None:
    """让 is_trading_hours/is_symbol_trading 用「真实日历 + 受控周日时钟」。

    不能直接放行（那就测不到日历），也不能依赖真实墙钟（周一开盘跑
    测试时 market_open=True 会创建日线 forming）。
    """
    from app.services.trading_hours import _COARSE_SESSIONS, _is_weekend_closed

    monkeypatch.setattr(kline, "datetime", _SundayClock)
    monkeypatch.setattr(
        kline,
        "is_trading_hours",
        lambda: not _is_weekend_closed(_SundayClock.now(_BJ), _COARSE_SESSIONS),
    )
    monkeypatch.setattr(
        kline,
        "is_symbol_trading",
        lambda symbol, now=None: not _is_weekend_closed(
            _SundayClock.now(_BJ), _COARSE_SESSIONS
        ),
    )


@pytest.mark.asyncio
async def test_update_realtime_klines_ignores_sunday_quotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """周日 vvtr 报价照推：聚合不得产出任何分钟 forming / 日线 forming。"""
    kline._realtime_state.clear()
    _real_calendar_on_sunday(monkeypatch)
    r = _FakeRedis()
    quotes = [
        {
            "symbol": "pp2701",
            "last_price": 8142.0,
            "volume": 206347,
            "position": 522241,
        }
    ]
    await update_realtime_klines(r, quotes)

    # 无任何 forming state / 缓存写入
    assert "pp2701" not in kline._realtime_state or not kline._realtime_state["pp2701"]
    for key in list(r.store):
        assert not key.startswith("kline:realtime:"), f"幽灵 forming 写入: {key}"
    kline._realtime_state.clear()


@pytest.mark.asyncio
async def test_update_realtime_klines_no_phantom_history_append(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """预置周日晚 forming state（历史遗留），周日报价不得把它固化进历史。"""
    hist_key = kline._history_cache_key("pp2701", "5m")
    existing = [
        {
            "time": "2026-08-21 14:00:00",
            "open": 8100,
            "high": 8110,
            "low": 8090,
            "close": 8105,
            "volume": 100,
        }
    ]
    phantom_state = {
        "bar": {
            "time": "2026-08-23 22:25:00",
            "open": 8142,
            "high": 8142,
            "low": 8142,
            "close": 8142,
            "volume": 0,
        },
        "bar_time": "2026-08-23 22:25:00",
        "period_start_volume": 0,
        "open_locked": True,
    }
    kline._realtime_state.clear()
    kline._realtime_state["pp2701"] = {"5m": phantom_state}
    _real_calendar_on_sunday(monkeypatch)
    r = _FakeRedis({hist_key: json.dumps(existing)})

    await update_realtime_klines(
        r, [{"symbol": "pp2701", "last_price": 8142.0, "volume": 206347}]
    )

    bars: list[dict[str, Any]] = json.loads(r.store[hist_key])
    times = [b["time"] for b in bars]
    assert "2026-08-23 22:25:00" not in times, "幽灵 bar 被固化进历史"
    kline._realtime_state.clear()


# ---------- 读路径过滤：源历史自带周末幽灵 bar（vvtr 上游实测） ----------


def test_period_end_never_lands_on_sunday_night() -> None:
    """交易分钟轴不得构造周日夜盘（_active_session_block 周末回退）。

    修复前：周一凌晨/周日晚的轴以「周日晚 21:00-23:00」为夜段，
    period_end_for_product 返回周日 23:00（未来）→ future 过滤放行
    周日幽灵 bar 固化进历史。
    """
    from datetime import date, time as dtime

    from app.services.session_profiles import period_end_for_product

    def not_sunday_phantom(end) -> bool:
        if end is None:
            return True
        # 不得落在周日晚（21:00 前的周日全天）——周五夜盘尾巴最晚周六凌晨
        if end.date() == date(2026, 8, 23):
            return False
        # 周一凌晨（09:00 前）无任何轴末端
        if end.date() == date(2026, 8, 24) and end.time() < dtime(9, 0):
            return False
        return True

    # 周一凌晨（au 夜盘到 02:30）
    assert not_sunday_phantom(period_end_for_product("AU", _dt(24, 0, 30), 60))
    # 周日晚（v 夜盘 21-23）
    assert not_sunday_phantom(period_end_for_product("V", _dt(23, 21, 30), 60))
    # 周日凌晨
    assert not_sunday_phantom(period_end_for_product("V", _dt(23, 3, 0), 5))

    # 正常交易日白天：周一 10:00 的 5m 周期末在日盘内（未来）
    end3 = period_end_for_product("V", _dt(24, 10, 2), 5)
    assert end3 is not None and _dt(24, 10, 0) < end3 <= _dt(24, 10, 10)


def test_filter_non_trading_bars_minutes() -> None:
    bars = [
        {"time": "2026-08-21 22:00:00"},  # 周五夜盘 → 保留
        {"time": "2026-08-22 01:00:00"},  # 周六凌晨尾巴 → 保留
        {"time": "2026-08-22 10:00:00"},  # 周六白天 → 剔除
        {"time": "2026-08-23 22:44:00"},  # 周日夜 → 剔除
        {"time": "2026-08-24 10:00:00"},  # 周一日盘 → 保留
    ]
    kept = [b["time"] for b in kline._filter_non_trading_bars(bars, "5m")]
    assert kept == [
        "2026-08-21 22:00:00",
        "2026-08-22 01:00:00",
        "2026-08-24 10:00:00",
    ]


def test_filter_non_trading_bars_daily() -> None:
    bars = [
        {"time": "2026-08-21"},  # 周五 → 保留
        {"time": "2026-08-22"},  # 周六 → 剔除
        {"time": "2026-08-23"},  # 周日 → 剔除
        {"time": "2026-08-24"},  # 周一 → 保留
        {"time": "not-a-date"},  # 解析失败 → 保守保留
    ]
    kept = [b["time"] for b in kline._filter_non_trading_bars(bars, "1d")]
    assert kept == ["2026-08-21", "2026-08-24", "not-a-date"]
