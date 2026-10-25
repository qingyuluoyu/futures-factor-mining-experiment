import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from datetime import date

import pytest

from app.data import stock_history


def install_history(monkeypatch, *, factors=None, bars=None):
    class Client:
        def query(self, name, params):
            if name == "daily":
                return bars if bars is not None else [{"ts_code": "000001.SZ", "trade_date": "20261015", "open": 10, "high": 12, "low": 9, "close": 11, "vol": 123.45}]
            return factors if factors is not None else [{"ts_code": "000001.SZ", "trade_date": "20261015", "adj_factor": 100}]
        def query_page(self, name, params):
            return SimpleNamespace(records=self.query(name, params), has_more=False)
    monkeypatch.setattr(stock_history, "get_client", lambda: Client())


@pytest.mark.parametrize("factor", [None, 0, -1, float("nan"), float("inf"), True])
def test_missing_or_invalid_adjustment_factor_never_falls_back_to_one(monkeypatch, factor):
    install_history(monkeypatch, factors=[] if factor is None else [{"ts_code": "000001.SZ", "trade_date": "20261015", "adj_factor": factor}])
    with pytest.raises(ValueError):
        stock_history.fetch_daily_batch(["000001"], "20261015", "20261016")


def test_complete_history_anchors_to_last_actual_bar_and_preserves_fractional_lots(monkeypatch):
    bars = [{"ts_code": "000001.SZ", "trade_date": day, "open": 10, "high": 12, "low": 9, "close": 11, "vol": 123.45} for day in ("20261015", "20261016")]
    factors = [{"ts_code": "000001.SZ", "trade_date": day, "adj_factor": value} for day, value in (("20261015", 100), ("20261016", 200), ("20261017", 300))]
    install_history(monkeypatch, factors=factors, bars=bars)
    result = stock_history.fetch_daily_batch(["000001"], "20261015", "20261018")["000001"]
    assert [record["close"] for record in result] == [5.5, 11]
    assert result[0]["volume"] == 123.45


@pytest.mark.parametrize("changes", [{"vol": None}, {"vol": float("nan")}, {"low": 99}, {"close": 0}, {"ts_code": "600001.SH"}, {"trade_date": "20261019"}])
def test_history_rejects_missing_volume_invalid_prices_unrequested_ids_and_dates(monkeypatch, changes):
    install_history(monkeypatch, bars=[{"ts_code": "000001.SZ", "trade_date": "20261015", "open": 10, "high": 12, "low": 9, "close": 11, "vol": 123, **changes}])
    with pytest.raises(ValueError):
        stock_history.fetch_daily_batch(["000001"], "20261015", "20261016")


def test_new_beijing_codes_are_not_misrouted_to_shanghai():
    assert stock_history._with_suffix("920000") == "920000.BJ"
    assert stock_history._with_suffix("600519") == "600519.SH"
    assert stock_history._with_suffix("000001") == "000001.SZ"


@pytest.mark.parametrize("has_more", [None, True])
def test_history_refuses_unconfirmed_pagination(monkeypatch, has_more):
    calls = []
    class Client:
        def query_page(self, name, params):
            calls.append(name)
            return SimpleNamespace(records=[], has_more=has_more)
    monkeypatch.setattr(stock_history, "get_client", lambda: Client())
    with pytest.raises(ValueError, match="分页"):
        stock_history.fetch_daily_batch(["000001"], "20261015", "20261016")
    assert calls == ["daily"]


@pytest.mark.parametrize("values", [(1e308, 1e-308), (1e-308, 1e308)])
def test_adjusted_prices_cannot_overflow_or_round_to_zero(monkeypatch, values):
    bars = [{"ts_code": "000001.SZ", "trade_date": day, "open": 10, "high": 12, "low": 9, "close": 11, "vol": 123} for day in ("20261015", "20261016")]
    factors = [{"ts_code": "000001.SZ", "trade_date": day, "adj_factor": value} for day, value in zip(("20261015", "20261016"), values)]
    install_history(monkeypatch, factors=factors, bars=bars)
    with pytest.raises(ValueError):
        stock_history.fetch_daily_batch(["000001"], "20261015", "20261016")


@pytest.mark.asyncio
async def test_integer_legacy_volume_column_refuses_lossy_fractional_lot_writes():
    script = Path(__file__).resolve().parents[2] / "scripts/import-stock-data.py"
    spec = importlib.util.spec_from_file_location("legacy_stock_import_guard", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    session = AsyncMock()
    with pytest.raises(ValueError, match="成交量"):
        await module._write_kline(session, "000001", [{"volume": 123.45}])
    assert not session.mock_calls


@pytest.mark.asyncio
async def test_integral_lots_use_exact_integer_database_values():
    script = Path(__file__).resolve().parents[2] / "scripts/import-stock-data.py"
    spec = importlib.util.spec_from_file_location("legacy_stock_import_valid", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    session = AsyncMock()
    session.execute.return_value = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))
    session.add_all = Mock()
    assert await module._write_kline(session, "000001", [{"trade_date": date(2026, 10, 16), "open": 10, "high": 11, "low": 9, "close": 10, "volume": 123.0}]) == 1
    written = session.add_all.call_args.args[0][0]
    assert type(written.volume) is int and written.volume == 123
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_legacy_qfq_rows_cannot_append_an_uncertified_new_adjustment_anchor():
    script = Path(__file__).resolve().parents[2] / "scripts/import-stock-data.py"
    spec = importlib.util.spec_from_file_location("legacy_stock_import_anchor", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    session = AsyncMock()
    session.execute.return_value = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [date(2026, 10, 15)]))
    session.add_all = Mock()
    with pytest.raises(ValueError, match="复权"):
        await module._write_kline(session, "000001", [{"trade_date": date(2026, 10, 16), "open": 10, "high": 11, "low": 9, "close": 10, "volume": 123}])
    session.add_all.assert_not_called()
    session.commit.assert_not_awaited()
