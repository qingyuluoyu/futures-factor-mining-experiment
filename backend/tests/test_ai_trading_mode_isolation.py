from decimal import Decimal
from datetime import datetime, timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.ai_trading import engine
from app.services.paper.price_band import resolve_last_price
from app.services import kline
from app.services import kline_virtual_hist
from app.services.kline_keys import history_kline_key


class ModelBoundaryReached(BaseException):
    pass


def task_fixture(mode):
    return SimpleNamespace(id=uuid4(), user_id=uuid4(), trading_mode=mode, symbol="rb2701", symbol_name="synthetic-test-only",
                           timeframe="5m", status="running", close_rules={}, stop_rules={}, strategy_type="ai", strategy_params={},
                           extra_timeframes=["15m", "60m"], side_mode="both", position_mode="fixed_qty", fixed_qty=1,
                           qty_min=1, qty_max=1, risk_style="balanced", max_hold_days=10, custom_prompt_enabled=False,
                           custom_prompt=None, position_opened_at=None, allocated_capital=Decimal("0"), ai_bars_limit=40,
                           last_bar_time=None, last_run_at=None, equity_baseline=100000, capital_usage_min_pct=0, capital_usage_max_pct=100)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["live", "virtual"])
@pytest.mark.parametrize("reference_missing", [False, True])
async def test_task_primary_extra_bars_and_reference_use_the_same_mode(monkeypatch, mode, reference_missing):
    task = task_fixture(mode)
    price = {"live": 100.0, "virtual": 500.0}
    calls = []

    async def bars(*args, trading_mode="live", **kwargs):
        calls.append((args[2], trading_mode))
        rows = [{"time": f"2026-10-16 09:{index:02d}:00", "open": price[trading_mode], "high": price[trading_mode],
                 "low": price[trading_mode], "close": price[trading_mode], "volume": 100} for index in range(40)]
        if args[2] == "5m":
            rows[-1]["close"] = 99999
        return rows

    async def reference(*args, trading_mode="live", **kwargs):
        calls.append(("reference", trading_mode))
        return None if reference_missing else Decimal(str(price[trading_mode]))

    captured = {}

    async def model_boundary(session, task, context):
        captured.update(context)
        raise ModelBoundaryReached()

    monkeypatch.setattr(engine, "get_session_status", lambda symbol: {"is_open": True})
    monkeypatch.setattr(engine, "get_kline_history", bars)
    monkeypatch.setattr(engine, "resolve_last_price", reference)
    monkeypatch.setattr(engine, "find_symbol_position", AsyncMock(return_value=None))
    monkeypatch.setattr(engine, "get_account_summary", AsyncMock(return_value={"total_equity": 100000, "available_margin": 100000}))
    monkeypatch.setattr(engine, "check_hard_rules", lambda **kwargs: None)
    monkeypatch.setattr(engine, "_check_exit_standards", lambda *args: None)
    monkeypatch.setattr(engine, "call_model_decision", model_boundary)
    session = AsyncMock()
    with pytest.raises(ModelBoundaryReached):
        await engine.evaluate_task(session, task, None, force=True)
    assert calls == [("5m", mode), ("reference", mode), ("15m", mode), ("60m", mode)]
    assert captured["last_price"] == price[mode]
    assert all(rows[-1]["close"] == price[mode] for rows in captured["extra_bars"].values())
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["live", "virtual"])
async def test_force_close_reference_matches_the_position_account(monkeypatch, mode):
    task = task_fixture(mode)
    reference = AsyncMock(return_value=Decimal("501"))
    execute = AsyncMock(return_value=(None, "synthetic-no-order"))
    monkeypatch.setattr(engine, "find_symbol_position", AsyncMock(return_value={"avg_price": 100, "quantity": 1, "available_quantity": 1}))
    monkeypatch.setattr(engine, "resolve_last_price", reference)
    monkeypatch.setattr(engine, "execute_action", execute)
    monkeypatch.setattr(engine, "_write_decision", AsyncMock())
    await engine.force_close_symbol(AsyncMock(), task, None, "manual_stop")
    assert reference.await_args.kwargs.get("trading_mode") == mode
    assert reference.await_args.kwargs["allow_mock"] is False
    assert execute.await_args.args[4] == 501


@pytest.mark.asyncio
async def test_virtual_quote_outage_never_falls_back_to_live_source_snapshot():
    class IsolatedRedis:
        def __init__(self):
            self.reads = []

        async def get(self, key):
            self.reads.append(key)
            return '{"last_price":100}' if key == "market:live:source:sina:quote:rb2701" else None

    redis = IsolatedRedis()
    assert await resolve_last_price("rb2701", redis, None, trading_mode="virtual", allow_mock=False) is None
    assert not any(key.startswith("market:live:") for key in redis.reads)
    assert await resolve_last_price("rb2701", redis, None, trading_mode="live", allow_mock=False) == Decimal("100")


@pytest.mark.asyncio
async def test_real_history_reader_uses_mode_specific_cache_without_external_fetch(monkeypatch):
    class SourceForbidden(BaseException):
        pass

    async def forbidden(*args, **kwargs):
        raise SourceForbidden("External fetch forbidden in cache isolation test")

    values = {}
    for mode, price in (("live", 100), ("virtual", 500)):
        rows = [{"time": (datetime(2026, 10, 16, 9) + timedelta(minutes=index)).strftime("%Y-%m-%d %H:%M:%S"),
                 "open": price, "high": price, "low": price, "close": price, "volume": 100,
                 "source": "seed" if index == 0 else mode} for index in range(300)]
        values[history_kline_key(mode, "rb2701", "1m")] = json.dumps(rows)
        values[f"market:{mode}:quote:rb2701"] = json.dumps({"last_price": price})

    class FixtureRedis:
        def __init__(self):
            self.reads = []

        async def get(self, key):
            self.reads.append(key)
            return values.get(key)

        async def set(self, key, value, **kwargs):
            values[key] = value

    monkeypatch.setattr(kline, "_build_latest_stale_gate", lambda *args: None)
    for name in ("_fetch_from_provider", "fetch_tickdata_kline", "_tickdata_first_when_closed"):
        monkeypatch.setattr(kline, name, forbidden)
    monkeypatch.setattr(kline_virtual_hist, "seed_virtual_history_from_sina", forbidden)
    for mode, price in (("virtual", 500), ("live", 100)):
        redis = FixtureRedis()
        rows = await kline.get_kline_history(redis, "rb2701", "1m", limit=2, trading_mode=mode)
        assert len(rows) == 2
        assert all(row["close"] == price for row in rows)
        assert history_kline_key(mode, "rb2701", "1m") in redis.reads
        other_mode = "live" if mode == "virtual" else "virtual"
        assert history_kline_key(other_mode, "rb2701", "1m") not in redis.reads
