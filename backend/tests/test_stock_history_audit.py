import asyncio
from datetime import date, datetime
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import Column, Date, Float, Integer, MetaData, String, Table, create_engine, event, insert, select

from app.models.stock import StockInstrument


@pytest.fixture
def database():
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    history = Table("kline_history", metadata,
        Column("id", Integer, primary_key=True), Column("market_type", String), Column("code", String),
        Column("trade_date", Date), Column("open", Float), Column("high", Float), Column("low", Float),
        Column("close", Float), Column("volume", Integer), Column("hold", Integer))
    metadata.create_all(engine)
    StockInstrument.__table__.create(engine)
    statements = []
    event.listen(engine, "before_cursor_execute", lambda connection, cursor, statement, parameters, context, many: statements.append(statement))
    with engine.begin() as connection:
        connection.execute(insert(StockInstrument), [{"code": "600001", "name": "测试股", "exchange": "SSE", "list_date": date(2020, 1, 1), "updated_at": datetime(2026, 10, 19)}])

        class Adapter:
            async def execute(self, statement):
                return connection.execute(statement)

        yield connection, Adapter(), history, statements
    engine.dispose()


def bar(**changes):
    return {"market_type": "stock", "code": "600001", "trade_date": date(2026, 10, 16),
        "open": 10, "high": 12, "low": 9, "close": 11, "volume": 100, "hold": 0, **changes}


def audit(adapter, **options):
    from app.services.stock_history_audit import audit_stock_history

    return asyncio.run(audit_stock_history(adapter, as_of=date(2026, 10, 19), **options))


def test_history_audit_is_stock_only_and_never_certifies_legacy_evidence(database):
    connection, adapter, history, statements = database
    connection.execute(insert(history), [bar(), bar(market_type="futures", code="AU", close=-10, volume=-1, trade_date=date(2030, 1, 1))])
    before = connection.execute(select(history)).all()
    statements.clear()
    result = audit(adapter)
    assert result["summary"]["rows"] == 1
    assert result["summary"]["securities"] == 1
    assert result["summary"]["catalog_securities"] == 1
    assert result["summary"]["catalog_securities_without_history"] == 0
    assert result["summary"]["earliest_trade_date"] == "2026-10-16"
    assert result["summary"]["latest_trade_date"] == "2026-10-16"
    assert result["quality"]["invalid_price_rows"] == 0
    assert result["quality"]["future_rows"] == 0
    assert result["evidence"]["unverified_rows"] == 1
    assert result["evidence"]["verification"] == "unverified_legacy_provenance_and_adjustment_anchor"
    assert result["evidence"]["independent_verification"] is False
    assert result["evidence"]["volume_unit"] == "lot_integer_legacy_storage"
    assert result["evidence"]["price_comparison"] == "not_performed_raw_eod_and_legacy_qfq_are_not_equivalent"
    assert result["source_calls"] == result["model_calls"] == 0
    assert len(statements) <= 5
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    assert connection.execute(select(history)).all() == before


def test_history_audit_counts_quality_issues_and_preserves_zero_volume_distinction(database):
    connection, adapter, history, statements = database
    connection.execute(insert(history), [bar(), bar(trade_date=date(2026, 10, 20)),
        bar(code="600002", close=0), bar(code="600003", open=float("inf")),
        bar(code="600004", high=8), bar(code="600005", volume=-1), bar(code="600006", volume=0),
        bar(code="bad", close=None), bar(trade_date=date(2019, 1, 1)), bar(code="600007", volume=None)])
    result = audit(adapter)
    assert result["summary"]["rows"] == 10
    assert result["quality"]["future_rows"] == 1
    assert result["quality"]["invalid_price_rows"] == 3
    assert result["quality"]["ohlc_inconsistent_rows"] == 1
    assert result["quality"]["invalid_volume_rows"] == 2
    assert result["quality"]["zero_volume_rows"] == 1
    assert result["quality"]["missing_catalog_rows"] == 7
    assert result["quality"]["invalid_code_rows"] == 1
    assert result["quality"]["before_listing_rows"] == 1
    assert result["summary"]["catalog_securities_with_history"] == 1


def test_history_audit_detects_duplicates_in_sql_without_cross_market_collision(database):
    connection, adapter, history, statements = database
    connection.execute(insert(history), [bar(), bar(), bar(market_type="futures")])
    result = audit(adapter)
    assert result["quality"]["duplicate_groups"] == 1
    assert result["quality"]["duplicate_extra_rows"] == 1
    assert len(result["samples"]) == 2
    assert all(sample["duplicate"] for sample in result["samples"])


def test_history_audit_samples_have_one_global_cap_and_only_public_identifiers(database):
    connection, adapter, history, statements = database
    connection.execute(insert(history), [bar(code=f"{index:06d}", close=0) for index in range(150)])
    result = audit(adapter, sample_limit=50)
    assert result["quality"]["invalid_price_rows"] == 150
    assert len(result["samples"]) == 50
    assert result["samples_truncated"] is True
    assert all(set(sample) <= {"id", "code", "trade_date", "invalid_code", "future", "invalid_price", "ohlc_inconsistent", "invalid_volume", "zero_volume", "missing_catalog", "before_listing", "duplicate"} for sample in result["samples"])


def test_history_audit_empty_history_is_explicit_and_not_certified(database):
    result = audit(database[1])
    assert result["summary"]["rows"] == 0
    assert result["summary"]["catalog_securities"] == 1
    assert result["summary"]["catalog_securities_without_history"] == 1
    assert result["summary"]["earliest_trade_date"] is None
    assert result["samples"] == []
    assert result["status"] == "no_history"
    assert result["evidence"]["independent_verification"] is False


@pytest.mark.parametrize("changes", [{"open": float("nan")}, {"high": float("-inf")},
    {"low": -1}, {"close": None}, {"open": 0}])
def test_history_audit_nonfinite_null_and_nonpositive_prices_are_never_accepted(database, changes):
    connection, adapter, history, statements = database
    connection.execute(insert(history), [bar(**changes)])
    result = audit(adapter)
    assert result["quality"]["invalid_price_rows"] == 1
    assert result["samples"][0]["invalid_price"] is True


@pytest.mark.parametrize("changes", [{"volume": float("inf")}, {"volume": float("nan")}, {"volume": -1}])
def test_history_audit_nonfinite_null_and_negative_volume_is_not_zero_filled(database, changes):
    connection, adapter, history, statements = database
    connection.execute(insert(history), [bar(**changes)])
    result = audit(adapter)
    assert result["quality"]["invalid_volume_rows"] == 1
    assert result["quality"]["zero_volume_rows"] == 0


@pytest.mark.parametrize("changes", [{"open": 13}, {"close": 13}, {"open": 8}, {"close": 8}, {"low": 13}])
def test_history_audit_checks_each_ohlc_bound(database, changes):
    connection, adapter, history, statements = database
    connection.execute(insert(history), [bar(**changes)])
    assert audit(adapter)["quality"]["ohlc_inconsistent_rows"] == 1


@pytest.mark.parametrize("sample_limit", [0, 51, -1, True, 2.5])
def test_history_audit_rejects_unbounded_or_invalid_sample_limit(database, sample_limit):
    with pytest.raises(ValueError, match="sample_limit"):
        audit(database[1], sample_limit=sample_limit)


@pytest.mark.parametrize("as_of", ["2026-10-19", datetime(2026, 10, 19), None])
def test_history_audit_requires_explicit_date_without_silently_changing_time_boundary(database, as_of):
    from app.services.stock_history_audit import audit_stock_history

    with pytest.raises(ValueError, match="as_of"):
        asyncio.run(audit_stock_history(database[1], as_of=as_of))


def load_cli():
    script = Path(__file__).resolve().parents[2] / "scripts/audit-stock-history.py"
    assert script.is_file(), "Missing read-only history audit CLI"
    spec = importlib.util.spec_from_file_location("stock_history_audit_cli", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_history_cli_existing_report_stops_before_settings_and_database(tmp_path, monkeypatch):
    module = load_cli()
    output = tmp_path / "existing.json"
    output.write_text("keep", encoding="utf-8")

    def forbidden():
        raise AssertionError("No settings or database access for an existing report")

    monkeypatch.setattr(module, "load_runtime_settings", forbidden)
    with pytest.raises(FileExistsError):
        asyncio.run(module.run(SimpleNamespace(output=str(output))))
    assert output.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("host,port", [("example.com", 5433), ("127.0.0.1", 5432)])
def test_history_cli_rejects_non_dedicated_database(tmp_path, monkeypatch, host, port):
    module = load_cli()
    monkeypatch.setattr(module, "load_runtime_settings", lambda: SimpleNamespace(postgres_host=host, postgres_port=port))
    with pytest.raises(ValueError, match="PG5433"):
        asyncio.run(module.run(SimpleNamespace(output=str(tmp_path / "new.json"))))
    assert not (tmp_path / "new.json").exists()
