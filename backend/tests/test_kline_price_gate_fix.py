"""K线价格门修复测试 —— ref_dt 解析与日线隔夜偏差放行

2026-08-25 夜盘实况:ma2701 白收 2747、夜盘跌至 2686(-2.27%),价格门因
`_live_ref_price` 解析 17 位 tick_time 失败(ref_dt=None)误把真实隔夜
偏差当脏价,全源拒掉导致 33 日线全空。本文件钉死这两个行为。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.services.kline import _build_price_gate, _live_ref_price


class _FakeRedis:
    """最小 Redis 桩:仅支持 get(key) 返回预置字符串"""

    def __init__(self, store: dict[str, str]) -> None:
        self._store = store

    async def get(self, key: str) -> str | None:
        return self._store.get(key)


def _quote(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


async def test_live_ref_price_parses_17digit_tick_time():
    """17 位 VVTR tick_time(或 tick_time_iso)能解析出 ref_dt"""
    r = _FakeRedis(
        {
            "market:live:quote:ma2701": _quote(
                {"last_price": 2686.0, "tick_time": "20260825221322000"}
            )
        }
    )
    price, ref_dt = await _live_ref_price(r, "ma2701")
    assert price == 2686.0
    assert ref_dt == datetime(2026, 8, 25, 22, 13, 22)


async def test_live_ref_price_prefers_iso_field():
    """quote 已带 tick_time_iso 时直接使用"""
    r = _FakeRedis(
        {
            "market:live:quote:ma2701": _quote(
                {"last_price": 2686.0, "tick_time": "垃圾值", "tick_time_iso": "2026-08-25 22:13:22"}
            )
        }
    )
    _, ref_dt = await _live_ref_price(r, "ma2701")
    assert ref_dt == datetime(2026, 8, 25, 22, 13, 22)


def _bars(close: float, day: str) -> list[dict[str, Any]]:
    return [{"time": day, "open": close, "high": close, "low": close, "close": close}]


def test_daily_gate_allows_real_overnight_move():
    """夜盘隔夜真实偏差放行:昨收 bar(15:00)与 22:13 基准差 >3h,2.27% 偏差不拒"""
    gate = _build_price_gate(2686.0, datetime(2026, 8, 25, 22, 13, 22), "1d")
    assert gate(_bars(2747.0, "2026-08-25")) is False


def test_daily_gate_rejects_same_day_dirty_price():
    """盘中同日脏价仍拦:基准 10:00 早于 bar 收盘 15:00,不享受放行"""
    gate = _build_price_gate(2686.0, datetime(2026, 8, 25, 10, 0, 0), "1d")
    assert gate(_bars(2760.0, "2026-08-25")) is True


def test_daily_gate_small_deviation_passes():
    """偏差 ≤2% 永远放行(与时间无关)"""
    gate = _build_price_gate(2686.0, datetime(2026, 8, 25, 10, 0, 0), "1d")
    assert gate(_bars(2700.0, "2026-08-25")) is False


def test_minute_gate_escape_still_works():
    """分钟线口径不变:基准远新于 bar(>3×周期)放行;同段大偏差拒"""
    ref = datetime(2026, 8, 25, 22, 13, 0)
    gate = _build_price_gate(2686.0, ref, "5m")
    # 同段 bar(22:10)偏差 3% → 拒
    assert gate(_bars(2770.0, "2026-08-25 22:10:00")) is True
    # 基准新于 bar 40 分钟(>3×5m)→ 放行(行情推进)
    assert gate(_bars(2770.0, "2026-08-25 21:30:00")) is False
