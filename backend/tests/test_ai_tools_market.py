"""Agent 行情/指标工具集成测试（mock 数据源）"""

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.tools.registry import execute_tool, get_tool_definitions


def test_tool_definitions_include_market_tools() -> None:
    names = {tool.name for tool in get_tool_definitions()}
    assert "get_quote" in names
    assert "get_kline" in names
    assert "calculate_indicator" in names
    assert "get_open_interest_series" in names
    assert "get_master_contract" in names
    assert "get_market_snapshot" in names


@pytest.mark.asyncio
async def test_execute_get_quote_from_sina() -> None:
    fake_quote = {
        "symbol": "rb2610",
        "name": "螺纹钢2610",
        "exchange": "上期所",
        "last_price": 3200.0,
        "change": 10.0,
        "change_pct": 0.31,
        "open_price": 3190.0,
        "high_price": 3210.0,
        "low_price": 3180.0,
        "pre_close": 3190.0,
        "bid_price": 3199.0,
        "ask_price": 3201.0,
        "volume": 100000,
        "position": 500000,
        "tick_time": "2026-07-22",
    }
    with patch(
        "app.connectors.sina.client.fetch_quote_via_bridge",
        return_value=fake_quote,
    ):
        raw = await execute_tool(
            "get_quote",
            {"symbol": "rb2610"},
            None,
            None,
            None,
        )
    payload = json.loads(raw)
    assert payload["success"] is True
    assert payload["quote"]["last_price"] == 3200.0
    assert payload["quote"]["open_interest"] == 500000
    assert payload["quote"]["pre_settle"] == 3190.0


def _fake_bars(count: int) -> list[dict[str, Any]]:
    bars: list[dict[str, Any]] = []
    price = 3000.0
    for index in range(count):
        price += 1.0
        bars.append(
            {
                "time": f"2026-07-01 {index:02d}:00:00",
                "open": price - 1,
                "high": price + 2,
                "low": price - 2,
                "close": price,
                "volume": 1000 + index * 10,
                "settle": price,
                "open_interest": 100000 + index * 100,
            }
        )
    return bars


@pytest.mark.asyncio
async def test_execute_get_kline_and_indicator() -> None:
    bars = _fake_bars(80)
    with patch(
        "app.data.kline_provider.fetch_kline_with_fallback",
        new=AsyncMock(return_value=bars),
    ):
        # 无 redis 时走 sina_direct 分支
        raw_kline = await execute_tool(
            "get_kline",
            {"symbol": "rb2610", "period": "5m", "limit": 60},
            None,
            None,
            None,
        )
        kline = json.loads(raw_kline)
        assert kline["success"] is True
        assert len(kline["bars"]) == 60
        assert "summary" in kline

        raw_macd = await execute_tool(
            "calculate_indicator",
            {
                "symbol": "rb2610",
                "indicator": "MACD",
                "period": "5m",
                "lookback": 80,
                "recent": 3,
            },
            None,
            None,
            None,
        )
        macd = json.loads(raw_macd)
        assert macd["success"] is True
        assert macd["indicator"] == "MACD"
        assert macd["latest"] is not None
        assert "dif" in macd["latest"]

        raw_vol = await execute_tool(
            "calculate_indicator",
            {
                "symbol": "rb2610",
                "indicator": "VOLUME",
                "period": "5m",
            },
            None,
            None,
            None,
        )
        vol = json.loads(raw_vol)
        assert vol["success"] is True
        assert vol["latest"] is not None
        assert "volume" in vol["latest"]


@pytest.mark.asyncio
async def test_execute_get_kline_via_service() -> None:
    bars = _fake_bars(30)
    redis_conn = AsyncMock()
    with patch(
        "app.services.ai.tools.market.quote_kline.get_kline_history",
        new=AsyncMock(return_value=bars),
    ) as mocked:
        raw = await execute_tool(
            "get_kline",
            {"symbol": "rb2610", "period": "1d", "limit": 20},
            None,
            None,
            redis_conn,
        )
    payload = json.loads(raw)
    assert payload["success"] is True
    assert payload["source"] == "kline_service"
    mocked.assert_awaited()


@pytest.mark.asyncio
async def test_open_interest_series() -> None:
    bars = _fake_bars(40)
    with patch(
        "app.data.kline_provider.fetch_kline_with_fallback",
        new=AsyncMock(return_value=bars),
    ):
        raw = await execute_tool(
            "get_open_interest_series",
            {"symbol": "rb2610", "period": "1d", "limit": 40},
            None,
            None,
            None,
        )
    payload = json.loads(raw)
    assert payload["success"] is True
    assert payload["oi_available"] is True
    assert payload["oi_last"] is not None


@pytest.mark.asyncio
async def test_get_kline_merges_realtime_bar() -> None:
    """历史末根与实时 forming bar 同 time 时应覆盖 close；quote 覆盖最新价/持仓。"""
    history = _fake_bars(5)
    last_time = history[-1]["time"]
    history[-1]["close"] = 3000.0
    rt_payload = {
        "symbol": "rb2610",
        "period": "5m",
        "bar": {
            "time": last_time,
            "open": 3000.0,
            "high": 3010.0,
            "low": 2990.0,
            "close": 3005.0,
            "volume": 123,
            "settle": None,
            "open_interest": None,
        },
    }
    redis_conn = AsyncMock()

    async def fake_get(key: str) -> str | None:
        if key.startswith("kline:realtime:"):
            return json.dumps(rt_payload)
        if key.startswith("market:quote:"):
            return json.dumps(
                {
                    "symbol": "rb2610",
                    "position": 888,
                    "last_price": 3011.0,
                    "pre_close": 2990.0,
                }
            )
        return None

    redis_conn.get = AsyncMock(side_effect=fake_get)

    with patch(
        "app.services.ai.tools.market.quote_kline.get_kline_history",
        new=AsyncMock(return_value=history),
    ):
        raw = await execute_tool(
            "get_kline",
            {"symbol": "rb2610", "period": "5m", "limit": 5},
            None,
            None,
            redis_conn,
        )
    payload = json.loads(raw)
    assert payload["success"] is True
    assert payload["realtime_merged"] is True
    # quote last_price 最终覆盖 close
    assert payload["bars"][-1]["close"] == 3011.0
    assert payload["bars"][-1]["open_interest"] == 888
    assert payload["summary"]["live_last_price"] == 3011.0


@pytest.mark.asyncio
async def test_get_kline_quote_only_when_rt_stale() -> None:
    """realtime time 落后于历史时，仍用 quote 刷新末根。"""
    history = _fake_bars(3)
    history[-1]["time"] = "2026-07-22 10:50:00"
    history[-1]["close"] = 100.0
    rt_payload = {
        "symbol": "rb2610",
        "period": "5m",
        "bar": {
            "time": "2026-07-22 10:45:00",
            "open": 99.0,
            "high": 101.0,
            "low": 98.0,
            "close": 100.5,
            "volume": 10,
            "settle": None,
            "open_interest": None,
        },
    }
    redis_conn = AsyncMock()

    async def fake_get(key: str) -> str | None:
        if key.startswith("kline:realtime:"):
            return json.dumps(rt_payload)
        if key.startswith("market:quote:"):
            return json.dumps({"symbol": "rb2610", "position": 9, "last_price": 102.0})
        return None

    redis_conn.get = AsyncMock(side_effect=fake_get)
    with patch(
        "app.services.ai.tools.market.quote_kline.get_kline_history",
        new=AsyncMock(return_value=history),
    ):
        raw = await execute_tool(
            "get_kline",
            {"symbol": "rb2610", "period": "5m", "limit": 3},
            None,
            None,
            redis_conn,
        )
    payload = json.loads(raw)
    assert payload["realtime_merged"] is True
    assert payload["merge_mode"] == "label_mismatch"
    assert payload["bars"][-1]["close"] == 102.0
    assert payload["bars"][-1]["open_interest"] == 9
