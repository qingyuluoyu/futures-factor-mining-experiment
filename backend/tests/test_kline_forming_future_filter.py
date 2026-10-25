"""周期结束标签的 forming K 线未来时间过滤回归。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.services import kline

_BJ_TZ = timezone(timedelta(hours=8))


class _FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 7, 29, 10, 31, tzinfo=_BJ_TZ)
        return value if tz is not None else value.replace(tzinfo=None)


class _FakeRedis:
    def __init__(self, bar: dict) -> None:
        self.payload = json.dumps({"bar": bar})

    async def get(self, key: str) -> str:
        return self.payload


def _bar(time: str) -> dict:
    return {
        "time": time,
        "open": 3000.0,
        "high": 3010.0,
        "low": 2990.0,
        "close": 3005.0,
        "volume": 100,
    }


def test_history_filter_allows_current_30m_period_end(monkeypatch) -> None:
    """10:31 时 10:45 是当前 30m forming 标签，不是幽灵未来数据。"""
    monkeypatch.setattr(kline, "datetime", _FixedDateTime)

    bars = kline._filter_bars_not_future(
        [_bar("2026-07-29 10:45:00"), _bar("2026-07-29 11:15:00")],
        "30m",
        "rb2610",
    )

    assert [bar["time"] for bar in bars] == ["2026-07-29 10:45:00"]


@pytest.mark.parametrize(
    ("period", "official_end", "future_end"),
    [
        ("5m", "2026-07-29 10:35:00", "2026-07-29 10:40:00"),
        ("15m", "2026-07-29 10:45:00", "2026-07-29 11:00:00"),
        ("30m", "2026-07-29 10:45:00", "2026-07-29 11:15:00"),
        ("60m", "2026-07-29 11:15:00", "2026-07-29 14:15:00"),
    ],
)
def test_history_filter_uses_each_official_forming_end(
    monkeypatch, period: str, official_end: str, future_end: str
) -> None:
    """5/15/30/60m 均允许当前结束标签，并拒绝下一根未来标签。"""
    monkeypatch.setattr(kline, "datetime", _FixedDateTime)

    bars = kline._filter_bars_not_future(
        [_bar(official_end), _bar(future_end)], period, "rb2610"
    )

    assert [bar["time"] for bar in bars] == [official_end]


@pytest.mark.asyncio
async def test_attach_realtime_allows_current_30m_period_end(monkeypatch) -> None:
    """实时 30m forming bar 应在整个周期内持续合并到图表。"""
    monkeypatch.setattr(kline, "datetime", _FixedDateTime)
    monkeypatch.setattr(kline, "is_trading_hours", lambda: True)
    bar = _bar("2026-07-29 10:45:00")

    bars = await kline._attach_realtime_bar(
        _FakeRedis(bar),
        "rb2610",
        "30m",
        [],
        "live",
    )

    assert bars == [bar | {"settle": None, "open_interest": None}]
