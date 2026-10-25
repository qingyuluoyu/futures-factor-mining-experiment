"""_attach_realtime_bar 同根合并单测 —— 字段级取优与 open>high 纠偏

线上问题（p2609 60m，2026-08-17 夜盘）：后端重启后 forming bar 从重建时刻
起步（丢窗口头部极值），旧合并规则 open 取历史 + H/L/C/V 全取 forming，
拼出自相矛盾 bar（open 9506 > high 9504，且丢真实高 9506/低 9480）。
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from app.services.kline import _attach_realtime_bar


def _redis_with_rt(rt: dict) -> AsyncMock:
    redis = AsyncMock()
    redis.get.return_value = json.dumps({"bar": rt})
    return redis


def _hist(**kw) -> dict:
    base = {
        "time": "2026-08-17 22:00:00",
        "open": 9506.0,
        "high": 9506.0,
        "low": 9480.0,
        "close": 9490.0,
        "volume": 9100,
        "settle": None,
        "open_interest": 117000,
    }
    base.update(kw)
    return base


def _rt(**kw) -> dict:
    # 重启丢头场景：21:36 重建后只反映 21:36-22:00 的行情
    base = {
        "time": "2026-08-17 22:00:00",
        "open": 9504.0,
        "high": 9504.0,
        "low": 9485.0,
        "close": 9499.0,
        "volume": 4494,
        "settle": None,
        "open_interest": 117300,
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_merge_takes_extremes_from_both_sides() -> None:
    """重启丢头：H/L 取两侧极值并集，close/volume 取优，open 保留历史"""
    bars = [_hist()]
    redis = _redis_with_rt(_rt())
    out = await _attach_realtime_bar(redis, "p2609", "60m", bars, "live")
    m = out[-1]
    assert m["open"] == 9506.0          # 历史开盘价（窗口起点）
    assert m["high"] == 9506.0          # max(历史 9506, forming 9504)
    assert m["low"] == 9480.0           # min(历史 9480, forming 9485)
    assert m["close"] == 9499.0         # realtime 最新
    assert m["volume"] == 9100          # 取较大者
    assert m["open_interest"] == 117300
    # 核心回归断言：不得出现 open > high
    assert m["high"] >= max(m["open"], m["close"])
    assert m["low"] <= min(m["open"], m["close"])


@pytest.mark.asyncio
async def test_merge_realtime_higher_extends_high() -> None:
    """正常场景：forming 创出比历史快照更高的价，high 跟随扩展"""
    bars = [_hist(high=9506.0, low=9490.0, volume=100)]
    redis = _redis_with_rt(_rt(high=9512.0, low=9488.0, volume=300))
    out = await _attach_realtime_bar(redis, "p2609", "60m", bars, "live")
    m = out[-1]
    assert m["high"] == 9512.0
    assert m["low"] == 9488.0
    assert m["volume"] == 300


@pytest.mark.asyncio
async def test_merge_dirty_history_never_outputs_inconsistent_bar() -> None:
    """兜底纠偏：即使历史侧带脏 high(<open)，输出也必须自洽"""
    bars = [_hist(open=9506.0, high=9500.0, low=9482.0)]
    redis = _redis_with_rt(_rt(high=9504.0, low=9485.0))
    out = await _attach_realtime_bar(redis, "p2609", "60m", bars, "live")
    m = out[-1]
    assert m["high"] >= m["open"]
    assert m["low"] <= m["open"]


@pytest.mark.asyncio
async def test_newer_realtime_key_appends() -> None:
    """forming 进入新周期：追加而非合并（保持既有行为）"""
    bars = [_hist(time="2026-08-17 21:00:00")]
    redis = _redis_with_rt(_rt(time="2026-08-17 22:00:00"))
    out = await _attach_realtime_bar(redis, "p2609", "60m", bars, "live")
    assert len(out) == 2
    assert out[-1]["time"] == "2026-08-17 22:00:00"
    assert out[-1]["open"] == 9504.0  # 新 bar 整条采用 forming
