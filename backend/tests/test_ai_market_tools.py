"""AI 行情工具测试 —— 今日大单聚合 / 盘口读取"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.models.big_order import TradeTick
from app.services.ai.tools.market.big_orders import tool_get_big_orders
from app.services.ai.tools.market.orderbook import tool_get_orderbook


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> "_Scalars":
        return _Scalars(self._rows)


class _Scalars(list):
    def all(self) -> "_Scalars":
        return self


class FakeSession:
    """仅支持 list_ticks 的 select(TradeTick) 查询"""

    def __init__(self, rows: list[TradeTick]) -> None:
        self.rows = rows
        self.executed: list[Any] = []

    async def execute(self, stmt: Any) -> _FakeResult:
        self.executed.append(stmt)
        return _FakeResult(list(self.rows))


class FakeRedis:
    def __init__(self, data: dict[str, str]) -> None:
        self.data = data

    async def get(self, key: str) -> str | None:
        return self.data.get(key)


def _tick(symbol: str, direction: str, price: float, volume: int, hh: int) -> TradeTick:
    return TradeTick(
        symbol=symbol,
        direction=direction,
        price=price,
        volume=volume,
        occurred_at=datetime(2026, 8, 26, hh, 0, 0, tzinfo=timezone.utc),
        trading_day=datetime(2026, 8, 26).date(),
    )


async def test_big_orders_aggregates_buy_sell():
    """大单工具：多空量聚合 + Top 排序 + 门槛过滤由 SQL 承担"""
    rows = [
        _tick("rb2610", "buy", 3200.0, 300, 10),
        _tick("rb2610", "buy", 3201.0, 150, 11),
        _tick("rb2610", "sell", 3199.0, 200, 12),
        _tick("rb2610", "sell", 3198.0, 100, 13),
    ]
    session = FakeSession(rows)
    result = await tool_get_big_orders(session, "RB2610", min_volume=100, limit=2000)
    assert result["success"] is True
    assert result["symbol"] == "rb2610"
    assert result["total_count"] == 4
    assert result["buy_volume"] == 450 and result["buy_count"] == 2
    assert result["sell_volume"] == 300 and result["sell_count"] == 2
    assert result["net_volume"] == 150
    # Top 按手数降序
    assert result["top"][0]["volume"] == 300
    assert result["top"][0]["direction"] == "buy"


async def test_big_orders_missing_symbol():
    result = await tool_get_big_orders(None, "  ")
    assert result["success"] is False


async def test_orderbook_prefers_panel_cache():
    """盘口工具：优先读盘口面板缓存（5档+stats 压平）"""
    panel = {
        "symbol": "rb2610",
        "asof": "2026-08-26T10:00:00+08:00",
        "stale": False,
        "asks": [{"price": 3201.0, "volume": 12}, {"price": 3202.0, "volume": 8}],
        "bids": [{"price": 3200.0, "volume": 15}],
        "stats": {
            "last_price": {"value": 3200.5, "source": "x", "quality": "ok"},
            "volume": {"value": 12345, "source": "x", "quality": "ok"},
        },
    }
    redis = FakeRedis({"market:live:orderbook:rb2610": json.dumps(panel)})
    result = await tool_get_orderbook(redis, "RB2610")
    assert result["success"] is True
    assert result["asks"][0]["price"] == 3201.0
    assert result["bids"][0]["volume"] == 15
    # stats {value,source,quality} → 压平为 {name: value}
    assert result["stats"]["last_price"] == 3200.5
    assert result["stats"]["volume"] == 12345


async def test_orderbook_falls_back_to_quote():
    """盘口缓存缺失：回退实时报价（买卖一档）"""
    redis = FakeRedis({})  # 无面板
    quote = {
        "last_price": 3200.5,
        "ask_price": 3201.0,
        "ask_vol": 12,
        "bid_price": 3200.0,
        "bid_vol": 15,
        "volume": 12345,
        "position": 100000,
    }
    import unittest.mock as mock

    with mock.patch(
        "app.services.ai.tools.market.orderbook.get_quote_json",
        return_value=quote,
    ):
        result = await tool_get_orderbook(redis, "rb2610")
    assert result["success"] is True
    assert result["source"] == "quote"
    assert result["asks"][0]["price"] == 3201.0
    assert result["stats"]["open_interest"] == 100000
