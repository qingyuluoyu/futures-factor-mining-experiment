import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.services.ai.execution_context import ExecutionContext
from app.services.ai.tools.registry import execute_tool


class ScopedRedis:
    def __init__(self, data):
        self.data = data
        self.keys = []

    async def get(self, key):
        self.keys.append(key)
        value = self.data.get(key)
        return json.dumps(value) if value is not None else None


def context(mode):
    return ExecutionContext(user_id=uuid4(), market_type="futures", trading_mode=mode, request_id=uuid4())


def bars(price, source="virtual"):
    return [{"time": f"2026-10-19 09:{index:02d}:00", "open": price, "high": price + 1, "low": price - 1, "close": price, "volume": 10, "source": source} for index in range(30)]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,price", [("live", 100), ("virtual", 500)])
async def test_quote_and_kline_follow_server_scope_not_model_arguments(mode, price):
    scope = context(mode)
    redis = ScopedRedis({"market:live:quote:rb2701": {"symbol": "rb2701", "last_price": 100}, "market:virtual:quote:rb2701": {"symbol": "rb2701", "last_price": 500, "source": "ctp:openctp_724"}, "kline:virtual:history:rb2701:5m": bars(500)})
    with patch("app.services.ai.tools.market.quote_kline.get_kline_history", new_callable=AsyncMock, return_value=bars(100, "live")), patch("app.connectors.sina.client.fetch_quote_via_bridge", side_effect=AssertionError("No source calls")):
        for name in ("get_quote", "get_kline"):
            payload = json.loads(await execute_tool(name, {"symbol": "rb2701", "period": "5m", "trading_mode": "live" if mode == "virtual" else "virtual"}, None, str(scope.user_id), redis, execution_context=scope))
            assert payload["success"] is True
            assert payload["trading_mode"] == mode
            observed = payload["quote"]["last_price"] if name == "get_quote" else payload["bars"][-1]["close"]
            assert observed == price
    if mode == "virtual":
        assert all("virtual" in key for key in redis.keys if key.startswith(("market:", "kline:")))


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [{"market:live:quote:rb2701": {"last_price": 100}}, {"kline:virtual:history:rb2701:5m": bars(100, "seed")}])
async def test_virtual_absence_never_falls_back_to_live_or_seeds(data):
    scope = context("virtual")
    redis = ScopedRedis(data)
    with patch("app.services.ai.tools.market.quote_kline.get_kline_history", new_callable=AsyncMock, side_effect=AssertionError("No implicit seeding")), patch("app.connectors.sina.client.fetch_quote_via_bridge", side_effect=AssertionError("No live quote")), patch("app.data.kline_provider.fetch_kline_with_fallback", new_callable=AsyncMock, side_effect=AssertionError("No external history")):
        for name in ("get_quote", "get_kline", "calculate_indicator"):
            payload = json.loads(await execute_tool(name, {"symbol": "rb2701", "indicator": "MA", "period": "5m"}, None, str(scope.user_id), redis, execution_context=scope))
            assert payload["success"] is False
            assert payload["error_code"] == "missing_market_data"
    assert all("virtual" in key for key in redis.keys if key.startswith(("market:", "kline:")))


@pytest.mark.asyncio
async def test_virtual_indicator_is_calculated_from_observed_virtual_bars():
    scope = context("virtual")
    redis = ScopedRedis({"kline:virtual:history:rb2701:5m": bars(500)})
    result = json.loads(await execute_tool("calculate_indicator", {"symbol": "rb2701", "indicator": "MA", "params": {"period": 5}, "period": "5m"}, None, str(scope.user_id), redis, execution_context=scope))
    assert result["success"] is True
    assert result["latest"]["value"] == 500
    assert result["trading_mode"] == "virtual"
    assert result["input_source"] == "virtual_namespace_cache"
    assert result["input_origin_verification"] == "archive_tick_origin_not_preserved"


@pytest.mark.asyncio
async def test_current_quote_or_older_forming_cannot_rewrite_virtual_history():
    scope = context("virtual")
    history = bars(500)
    redis = ScopedRedis({"market:virtual:quote:rb2701": {"last_price": 999, "source": "ctp:openctp_724"}, "kline:virtual:history:rb2701:5m": history, "kline:virtual:realtime:rb2701:5m": {"bar": {**history[0], "open": 888, "high": 889, "low": 887, "close": 888}}})
    result = json.loads(await execute_tool("get_kline", {"symbol": "rb2701", "period": "5m"}, None, str(scope.user_id), redis, execution_context=scope))
    assert result["success"] is True
    assert result["bars"][-1]["close"] == 500


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_price", [float("nan"), float("inf"), 0, -1, True])
async def test_virtual_quote_rejects_invalid_price(bad_price):
    scope = context("virtual")
    redis = ScopedRedis({"market:virtual:quote:rb2701": {"last_price": bad_price}})
    payload = json.loads(await execute_tool("get_quote", {"symbol": "rb2701"}, None, str(scope.user_id), redis, execution_context=scope))
    assert payload["success"] is False
    assert payload["error_code"] == "invalid_market_data"


@pytest.mark.asyncio
async def test_conflicting_virtual_bars_and_missing_volume_are_not_silently_repaired():
    scope = context("virtual")
    history = bars(500)
    conflict = {**history[0], "open": 600, "high": 601, "low": 599, "close": 600}
    redis = ScopedRedis({"kline:virtual:history:rb2701:5m": history + [conflict]})
    payload = json.loads(await execute_tool("get_kline", {"symbol": "rb2701", "period": "5m"}, None, str(scope.user_id), redis, execution_context=scope))
    assert payload["error_code"] == "invalid_market_data"
    redis.data["kline:virtual:history:rb2701:5m"] = [{**bar, "volume": None} for bar in history]
    payload = json.loads(await execute_tool("calculate_indicator", {"symbol": "rb2701", "period": "5m", "indicator": "VOLUME"}, None, str(scope.user_id), redis, execution_context=scope))
    assert payload["error_code"] == "missing_market_data"


@pytest.mark.asyncio
async def test_seed_exclusion_and_unordered_identical_duplicates_are_visible():
    scope = context("virtual")
    history = bars(500)
    redis = ScopedRedis({"kline:virtual:history:rb2701:5m": [*reversed(history), history[0], *bars(100, "seed")]})
    payload = json.loads(await execute_tool("get_kline", {"symbol": "rb2701", "period": "5m"}, None, str(scope.user_id), redis, execution_context=scope))
    assert payload["success"] is True
    assert payload["excluded_seed_or_unknown_bars"] == 30
    assert len(payload["bars"]) == 30
    assert [bar["time"] for bar in payload["bars"]] == sorted(bar["time"] for bar in payload["bars"])


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["get_quote", "get_kline"])
async def test_virtual_cache_parse_failure_is_distinct_from_absence(name):
    scope = context("virtual")

    class CorruptRedis:
        async def get(self, key):
            return "{bad" if key.startswith("market:") else None

    payload = json.loads(await execute_tool(name, {"symbol": "rb2701", "period": "5m"}, None, str(scope.user_id), CorruptRedis(), execution_context=scope))
    assert payload["success"] is False
    assert payload["error_code"] == "invalid_market_data"


@pytest.mark.asyncio
async def test_equal_time_forming_cannot_overwrite_closed_virtual_bar():
    scope = context("virtual")
    history = bars(500)
    redis = ScopedRedis({"kline:virtual:history:rb2701:5m": history, "kline:virtual:realtime:rb2701:5m": {"bar": {**history[-1], "open": 600, "high": 601, "low": 599, "close": 600}}})
    payload = json.loads(await execute_tool("get_kline", {"symbol": "rb2701", "period": "5m"}, None, str(scope.user_id), redis, execution_context=scope))
    assert payload["success"] is True
    assert payload["bars"][-1]["close"] == 500


@pytest.mark.asyncio
async def test_incomplete_indicator_window_does_not_report_success_with_no_value():
    scope = context("virtual")
    redis = ScopedRedis({"kline:virtual:history:rb2701:5m": bars(500)[:3]})
    payload = json.loads(await execute_tool("calculate_indicator", {"symbol": "rb2701", "period": "5m", "indicator": "MA", "params": {"period": 20}}, None, str(scope.user_id), redis, execution_context=scope))
    assert payload["success"] is False
    assert payload["error_code"] == "insufficient_market_data"


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("high_price", float("nan")), ("volume", -1), ("change", float("inf")), ("position", True)])
async def test_quote_optional_numeric_fields_do_not_leak_invalid_json(field, value):
    scope = context("virtual")
    redis = ScopedRedis({"market:virtual:quote:rb2701": {"last_price": 500, field: value}})
    payload = json.loads(await execute_tool("get_quote", {"symbol": "rb2701"}, None, str(scope.user_id), redis, execution_context=scope))
    assert payload["success"] is False
    assert payload["error_code"] == "invalid_market_data"


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["sina_virtual_fill", "ctp:openctp_724+pulse", "", "unverified"])
async def test_virtual_quote_cannot_disguise_live_fill_or_pulse_as_observed_quote(source):
    scope = context("virtual")
    redis = ScopedRedis({"market:virtual:quote:rb2701": {"last_price": 500, "source": source}})
    payload = json.loads(await execute_tool("get_quote", {"symbol": "rb2701"}, None, str(scope.user_id), redis, execution_context=scope))
    assert payload["success"] is False
    assert payload["error_code"] == "incompatible_market_source"


@pytest.mark.asyncio
async def test_volume_average_requires_its_requested_full_window():
    scope = context("virtual")
    redis = ScopedRedis({"kline:virtual:history:rb2701:5m": bars(500)[:3]})
    payload = json.loads(await execute_tool("calculate_indicator", {"symbol": "rb2701", "period": "5m", "indicator": "VOLUME", "params": {"ma_period": 20}}, None, str(scope.user_id), redis, execution_context=scope))
    assert payload["success"] is False
    assert payload["error_code"] == "insufficient_market_data"
