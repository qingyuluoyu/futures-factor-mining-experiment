"""tickdata 新鲜度守卫测试 —— 上游断供时近期请求降级、历史请求不受影响"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

from app.data import tickdata_kline as td


def _bars(times: list[str]) -> list[dict]:
    return [{"time": t, "open": 1.0, "high": 1.0, "low": 1.0,
             "close": 1.0, "volume": 1} for t in times]


def test_recent_anchored():
    now = datetime.now()
    assert td._recent_anchored(None) is True
    assert td._recent_anchored(now - timedelta(days=3)) is True
    assert td._recent_anchored(now - timedelta(days=60)) is False


def test_bars_stale():
    ref = datetime(2026, 8, 13)
    fresh = _bars(["2026-08-12 14:45:00"])
    stale = _bars(["2026-06-15 14:45:00"])
    assert td._bars_stale(fresh, ref) is False
    assert td._bars_stale(stale, ref) is True
    assert td._bars_stale([], ref) is False
    assert td._bars_stale(_bars([""]), ref) is False  # 无时间戳不判过期


def test_fetch_degrades_on_stale_recent_request(monkeypatch):
    """近期请求 + 库内数据两个月前 → 返回空（降级信号）

    rows 为 pg-tickdata 真实口径的 eob（周期终点）标签，取周一 2026-06-15
    日盘内 15 分钟网格——周末/时段外时刻会被交易分钟轴归一折叠，夹具
    必须落在真实交易时段内。
    """
    rows = [
        {
            "ts": datetime(2026, 6, 15, 9, 15) + timedelta(minutes=15 * i),
            "open": 1, "high": 1,
            "low": 1, "close": 1, "volume": 1, "position": 1,
        }
        for i in range(5)
    ]

    class _Conn:
        async def fetch(self, *a, **k):
            return rows

    class _Acq:
        async def __aenter__(self):
            return _Conn()

        async def __aexit__(self, *a):
            return False

    class _Pool:
        def acquire(self):
            return _Acq()

    async def _fake_pool():
        return _Pool()

    monkeypatch.setattr(td, "_get_pool", _fake_pool)
    import asyncio

    # 近期模式（end_time 空）：最新 bar 为 2026-06 → 降级返回 []
    out = asyncio.run(td.fetch_tickdata_kline("rb2610", "15m", end_time="", limit=5))
    assert out == []

    # 历史锚点（end 落在数据覆盖期内）：正常返回
    out2 = asyncio.run(td.fetch_tickdata_kline(
        "rb2610", "15m", end_time="2026-06-15 23:59:59", limit=5))
    # end 距今 >14 天 → 非近期锚定 → 不过守卫；窗口内 5 根 → 命中返回
    assert len(out2) == 5
