from contextlib import asynccontextmanager
from datetime import date, datetime
import importlib
import importlib.util
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from app.models.kline_history import KlineHistory
from app.models.stock import StockFinancial, StockInstrument, StockValuation
from app.services.stock_factors.engine import BacktestParams, PreprocessConfig, ResearchDataError, run_portfolio_backtest


def snapshot_module():
    assert importlib.util.find_spec("app.services.stock_factors.snapshots") is not None
    return importlib.import_module("app.services.stock_factors.snapshots")


@asynccontextmanager
async def snapshot_database(*, missing_price=False):
    database = create_engine("sqlite:///:memory:")
    for model in (KlineHistory, StockInstrument, StockValuation, StockFinancial):
        model.__table__.create(database)
    with Session(database) as session:
        for position, code in enumerate(("600001", "600002")):
            session.add(StockInstrument(code=code, name=f"证券{position}", exchange="SSE", industry="金融", list_date=date(2020, 1, 1), updated_at=datetime(2024, 1, 1)))
            for offset, trading_date in enumerate((date(2024, 1, 2), date(2024, 1, 3))):
                if not (missing_price and position == 1 and offset == 1):
                    session.add(KlineHistory(market_type="stock", code=code, trade_date=trading_date, open=10, high=11, low=9, close=10 + position + offset, volume=100))
                session.add(StockValuation(code=code, trade_date=trading_date, pb=None if offset == 0 else 2 + position, pe_ttm=-10, total_mv=100, turnover_rate=1))
            session.add(StockFinancial(code=code, report_date=date(2023, 9, 30), publish_date=date(2023, 10, 30), roe=10 + position, gross_margin=None, updated_at=datetime(2024, 1, 1)))
        session.add(KlineHistory(market_type="futures", code="AU", trade_date=date(2024, 1, 2), open=10, high=11, low=9, close=10, volume=100))
        session.add(StockFinancial(code="600001", report_date=date(2023, 12, 31), publish_date=date(2024, 4, 1), roe=99, updated_at=datetime(2024, 1, 1)))
        session.add(StockFinancial(code="600002", report_date=date(2024, 12, 31), publish_date=date(2023, 12, 31), roe=99, updated_at=datetime(2024, 1, 1)))
        session.commit()

    class DatabaseSession:
        async def __aenter__(self):
            self.session = Session(database)
            return self

        async def __aexit__(self, *args):
            self.session.close()

        def get_bind(self):
            return self.session.get_bind()

        def begin(self):
            class Transaction:
                async def __aenter__(transaction):
                    transaction.context = self.session.begin()
                    transaction.context.__enter__()

                async def __aexit__(transaction, *args):
                    transaction.context.__exit__(*args)
            return Transaction()

        async def connection(self, **kwargs):
            return self.session.connection(**kwargs)

        async def execute(self, statement):
            return self.session.execute(statement)

    try:
        yield DatabaseSession, database
    finally:
        database.dispose()


@pytest.mark.asyncio
async def test_real_sql_snapshot_preserves_missing_values_and_prior_disclosures():
    snapshots = snapshot_module()
    async with snapshot_database(missing_price=True) as (factory, database):
        market, manifest = await snapshots.load_versioned_market_data(factory, date(2024, 1, 2), date(2024, 1, 3))
    assert list(market.close.columns) == ["600001", "600002"]
    assert np.isnan(market.close.loc["2024-01-03", "600002"])
    assert np.isnan(market.valuation["pb"].loc["2024-01-02", "600001"])
    assert market.financial["roe"].loc["2024-01-02", "600001"] == 10
    assert market.financial["roe"].loc["2024-01-02", "600002"] == 11
    assert np.isnan(market.financial["gross_margin"].loc["2024-01-02", "600001"])
    assert len(manifest["data_version"]) == 64
    assert manifest["row_counts"] == {"prices": 3, "valuations": 4, "financials": 2, "instruments": 2}
    assert manifest["coverage"]["missing_price_cells"] == 1
    assert manifest["adjustment"] == "forward_adjusted_unversioned"
    assert manifest["point_in_time_certified"] is False
    assert manifest["immutable_archive"] is False
    json.dumps(manifest, allow_nan=False)


@pytest.mark.asyncio
async def test_fingerprint_is_stable_and_tracks_values_missingness_and_metadata():
    snapshots = snapshot_module()
    async with snapshot_database() as (factory, database):
        first_market, first = await snapshots.load_versioned_market_data(factory, date(2024, 1, 2), date(2024, 1, 3))
        second_market, second = await snapshots.load_versioned_market_data(factory, date(2024, 1, 2), date(2024, 1, 3))
        assert first["data_version"] == second["data_version"]
        pd.testing.assert_frame_equal(first_market.close, second_market.close)
        with database.begin() as connection:
            connection.execute(text("UPDATE stock_valuation SET pb = 0 WHERE code = '600001' AND pb IS NULL"))
        _, changed = await snapshots.load_versioned_market_data(factory, date(2024, 1, 2), date(2024, 1, 3))
        assert first["data_version"] != changed["data_version"]
        with database.begin() as connection:
            connection.execute(text("UPDATE stock_instruments SET industry = '科技' WHERE code = '600001'"))
        _, reclassified = await snapshots.load_versioned_market_data(factory, date(2024, 1, 2), date(2024, 1, 3))
        assert changed["data_version"] != reclassified["data_version"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", ["MAX_PRICE_ROWS", "MAX_VALUATION_ROWS", "MAX_FINANCIAL_ROWS", "MAX_INSTRUMENTS", "MAX_PANEL_CELLS"])
async def test_each_limit_rejects_instead_of_truncating(bound, monkeypatch):
    snapshots = snapshot_module()
    monkeypatch.setattr(snapshots, bound, 1)
    async with snapshot_database() as (factory, database):
        with pytest.raises(ResearchDataError) as raised:
            await snapshots.load_versioned_market_data(factory, date(2024, 1, 2), date(2024, 1, 3))
    assert raised.value.code == "snapshot_limit_exceeded"


@pytest.mark.asyncio
@pytest.mark.parametrize("statement", [
    "UPDATE kline_history SET close = 1e999 WHERE code = '600001'",
    "UPDATE stock_valuation SET pb = 1e999 WHERE code = '600001'",
    "UPDATE stock_financial SET roe = 1e999 WHERE code = '600001'",
    "UPDATE kline_history SET code = 'unknown' WHERE code = '600001'",
    "UPDATE stock_instruments SET exchange = 'UNKNOWN' WHERE code = '600001'",
])
async def test_invalid_input_data_is_explicitly_rejected(statement):
    snapshots = snapshot_module()
    async with snapshot_database() as (factory, database):
        with database.begin() as connection:
            connection.execute(text(statement))
        with pytest.raises(ResearchDataError) as raised:
            await snapshots.load_versioned_market_data(factory, date(2024, 1, 2), date(2024, 1, 3))
    assert raised.value.code == "invalid_market_data"


@pytest.mark.asyncio
async def test_future_listing_is_excluded_from_current_category_mapping():
    snapshots = snapshot_module()
    async with snapshot_database() as (factory, database):
        with database.begin() as connection:
            connection.execute(text("UPDATE stock_instruments SET list_date = '2025-01-01' WHERE code = '600002'"))
        market, manifest = await snapshots.load_versioned_market_data(factory, date(2024, 1, 2), date(2024, 1, 3))
    assert "600002" not in market.industry
    assert "600002" not in market.name


@pytest.mark.asyncio
async def test_same_day_annual_and_first_quarter_disclosures_select_latest_report_after_publication():
    snapshots = snapshot_module()
    async with snapshot_database() as (factory, database):
        with Session(database) as session:
            session.execute(text("DELETE FROM stock_financial"))
            session.add(StockFinancial(code="600001", report_date=date(2022, 12, 31), publish_date=date(2024, 1, 2), roe=90, updated_at=datetime(2024, 1, 1)))
            session.add(StockFinancial(code="600001", report_date=date(2023, 3, 31), publish_date=date(2024, 1, 2), roe=30, updated_at=datetime(2024, 1, 1)))
            session.commit()
        market, manifest = await snapshots.load_versioned_market_data(factory, date(2024, 1, 2), date(2024, 1, 3))
    assert np.isnan(market.financial["roe"].loc["2024-01-02", "600001"])
    assert market.financial["roe"].loc["2024-01-03", "600001"] == 30
    assert manifest["financial_rows"] == {"raw": 2, "selected": 1}
    assert manifest["financial_selection_version"]


@pytest.mark.asyncio
async def test_later_publication_of_older_report_never_regresses_available_report_frontier():
    snapshots = snapshot_module()
    async with snapshot_database() as (factory, database):
        with Session(database) as session:
            session.add(StockFinancial(code="600001", report_date=date(2023, 3, 31), publish_date=date(2024, 1, 2), roe=90, updated_at=datetime(2024, 1, 1)))
            session.commit()
        market, manifest = await snapshots.load_versioned_market_data(factory, date(2024, 1, 2), date(2024, 1, 3))
    assert market.financial["roe"].loc["2024-01-02", "600001"] == 10
    assert market.financial["roe"].loc["2024-01-03", "600001"] == 10
    assert manifest["financial_rows"] == {"raw": 3, "selected": 2}


@pytest.mark.asyncio
async def test_exact_duplicate_security_report_period_is_rejected():
    snapshots = snapshot_module()
    async with snapshot_database() as (factory, database):
        with database.begin() as connection:
            connection.execute(text("CREATE TABLE financial_unconstrained AS SELECT * FROM stock_financial"))
            connection.execute(text("DROP TABLE stock_financial"))
            connection.execute(text("ALTER TABLE financial_unconstrained RENAME TO stock_financial"))
            connection.execute(text("INSERT INTO stock_financial SELECT * FROM stock_financial WHERE code = '600001' AND report_date = '2023-09-30'"))
        with pytest.raises(ResearchDataError) as raised:
            await snapshots.load_versioned_market_data(factory, date(2024, 1, 2), date(2024, 1, 3))
    assert raised.value.code == "invalid_market_data"


@pytest.mark.asyncio
async def test_real_sql_momentum_backtest_remains_available_with_same_day_reports():
    snapshots = snapshot_module()
    async with snapshot_database() as (factory, database):
        with Session(database) as session:
            session.add(StockFinancial(code="600001", report_date=date(2023, 6, 30), publish_date=date(2023, 10, 30), roe=90, updated_at=datetime(2024, 1, 1)))
            for position, code in enumerate(("600001", "600002")):
                for offset, trading_date in enumerate(pd.bdate_range("2024-01-04", "2024-04-30")):
                    close = 100 * (1.001 + position * 0.001) ** offset
                    session.add(KlineHistory(market_type="stock", code=code, trade_date=trading_date.date(), open=close, high=close, low=close, close=close, volume=100))
            session.commit()
        market, manifest = await snapshots.load_versioned_market_data(factory, date(2024, 1, 2), date(2024, 4, 30))
    result = run_portfolio_backtest(market, BacktestParams(factor_ids=["ret_20d"], top_n=1, commission_rate=0, slippage_rate=0, preprocess=PreprocessConfig(winsorize="none", fillna=False)))
    assert "error" not in result, result
    assert result["nav"]
    assert result["nav"][-1]["nav"] > 1
    assert manifest["financial_rows"] == {"raw": 4, "selected": 3}


@pytest.mark.asyncio
async def test_financial_selection_version_is_part_of_content_fingerprint(monkeypatch):
    snapshots = snapshot_module()
    async with snapshot_database() as (factory, database):
        _, before = await snapshots.load_versioned_market_data(factory, date(2024, 1, 2), date(2024, 1, 3))
        monkeypatch.setattr(snapshots, "FINANCIAL_SELECTION_VERSION", "test-financial-selection-v2")
        _, after = await snapshots.load_versioned_market_data(factory, date(2024, 1, 2), date(2024, 1, 3))
    assert before["data_version"] != after["data_version"]
    assert after["financial_selection_version"] == "test-financial-selection-v2"


@pytest.mark.asyncio
async def test_fingerprint_retains_disclosures_not_selected_for_calculation():
    snapshots = snapshot_module()
    async with snapshot_database() as (factory, database):
        with Session(database) as session:
            session.add(StockFinancial(code="600001", report_date=date(2023, 6, 30), publish_date=date(2023, 10, 30), roe=90, updated_at=datetime(2024, 1, 1)))
            session.commit()
        before_market, before = await snapshots.load_versioned_market_data(factory, date(2024, 1, 2), date(2024, 1, 3))
        with database.begin() as connection:
            connection.execute(text("UPDATE stock_financial SET roe = 99 WHERE report_date = '2023-06-30'"))
        after_market, after = await snapshots.load_versioned_market_data(factory, date(2024, 1, 2), date(2024, 1, 3))
    assert before["data_version"] != after["data_version"]
    pd.testing.assert_frame_equal(before_market.financial["roe"], after_market.financial["roe"])


@pytest.mark.asyncio
async def test_latest_report_missing_value_does_not_fall_back_to_older_report_value():
    snapshots = snapshot_module()
    async with snapshot_database() as (factory, database):
        with Session(database) as session:
            session.add(StockFinancial(code="600001", report_date=date(2023, 6, 30), publish_date=date(2023, 10, 30), roe=90, updated_at=datetime(2024, 1, 1)))
            session.execute(text("UPDATE stock_financial SET roe = NULL WHERE code = '600001' AND report_date = '2023-09-30'"))
            session.commit()
        market, manifest = await snapshots.load_versioned_market_data(factory, date(2024, 1, 2), date(2024, 1, 3))
    assert np.isnan(market.financial["roe"].loc["2024-01-02", "600001"])
    assert manifest["financial_rows"] == {"raw": 3, "selected": 2}


@pytest.mark.asyncio
async def test_query_limits_and_explicit_transaction_are_applied_to_real_sql():
    snapshots = snapshot_module()
    statements = []
    async with snapshot_database() as (factory, database):
        def record_statement(connection, cursor, statement, parameters, context, executemany):
            statements.append((statement, parameters))

        event.listen(database, "before_cursor_execute", record_statement)
        await snapshots.load_versioned_market_data(factory, date(2024, 1, 2), date(2024, 1, 3))
    assert statements[0][0] == "BEGIN"
    selections = [(statement, parameters) for statement, parameters in statements if statement.startswith("SELECT")]
    assert len(selections) == 4
    for (statement, parameters), limit in zip(selections, (snapshots.MAX_PRICE_ROWS, snapshots.MAX_VALUATION_ROWS, snapshots.MAX_FINANCIAL_ROWS, snapshots.MAX_INSTRUMENTS), strict=True):
        assert "ORDER BY" in statement
        assert "LIMIT" in statement
        assert parameters[-2] == limit + 1


@pytest.mark.asyncio
@pytest.mark.parametrize("statement", [
    "UPDATE kline_history SET close = close + 1 WHERE code = '600001'",
    "UPDATE stock_financial SET roe = roe + 1 WHERE code = '600001'",
    "UPDATE stock_instruments SET name = '改名' WHERE code = '600001'",
])
async def test_fingerprint_tracks_each_calculation_input(statement):
    snapshots = snapshot_module()
    async with snapshot_database() as (factory, database):
        _, before = await snapshots.load_versioned_market_data(factory, date(2024, 1, 2), date(2024, 1, 3))
        with database.begin() as connection:
            connection.execute(text(statement))
        _, after = await snapshots.load_versioned_market_data(factory, date(2024, 1, 2), date(2024, 1, 3))
    assert before["data_version"] != after["data_version"]


@pytest.mark.asyncio
async def test_future_rows_do_not_change_the_input_fingerprint():
    snapshots = snapshot_module()
    async with snapshot_database() as (factory, database):
        _, before = await snapshots.load_versioned_market_data(factory, date(2024, 1, 2), date(2024, 1, 3))
        with database.begin() as connection:
            connection.execute(text("UPDATE stock_financial SET roe = 999 WHERE publish_date > '2024-01-03' OR report_date > '2024-01-03'"))
        _, after = await snapshots.load_versioned_market_data(factory, date(2024, 1, 2), date(2024, 1, 3))
    assert before["data_version"] == after["data_version"]


@pytest.mark.asyncio
async def test_duplicate_prices_fail_explicitly_even_without_database_constraint():
    snapshots = snapshot_module()
    async with snapshot_database() as (factory, database):
        with database.begin() as connection:
            connection.execute(text("CREATE TABLE kline_unconstrained AS SELECT * FROM kline_history"))
            connection.execute(text("DROP TABLE kline_history"))
            connection.execute(text("ALTER TABLE kline_unconstrained RENAME TO kline_history"))
            connection.execute(text("INSERT INTO kline_history SELECT * FROM kline_history WHERE code = '600001' LIMIT 1"))
        with pytest.raises(ResearchDataError) as raised:
            await snapshots.load_versioned_market_data(factory, date(2024, 1, 2), date(2024, 1, 3))
    assert raised.value.code == "invalid_market_data"


@pytest.mark.asyncio
async def test_empty_range_has_explicit_missing_data_error():
    snapshots = snapshot_module()
    async with snapshot_database() as (factory, database):
        with pytest.raises(ResearchDataError) as raised:
            await snapshots.load_versioned_market_data(factory, date(2020, 1, 1), date(2020, 1, 2))
    assert raised.value.code == "missing_market_data"
    assert raised.value.status == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("start,end", [
    (date(2024, 2, 1), date(2024, 1, 1)),
    (date(2000, 1, 1), date(2024, 1, 1)),
    ("2024-01-01", date(2024, 1, 2)),
])
async def test_invalid_dates_fail_before_creating_a_database_session(start, end):
    snapshots = snapshot_module()
    with pytest.raises(ResearchDataError) as raised:
        await snapshots.load_versioned_market_data(None, start, end)
    assert raised.value.code == "invalid_date_range"


@pytest.mark.asyncio
@pytest.mark.parametrize("isolation,read_only", [
    ("read committed", "on"),
    ("repeatable read", "off"),
    ("serializable", "on"),
    (None, "on"),
])
async def test_postgresql_settings_fail_closed_when_snapshot_guarantee_is_absent(isolation, read_only):
    snapshots = snapshot_module()
    assert hasattr(snapshots, "_verify_transaction_settings")
    statements = []

    class PostgreSQLSettings:
        async def execute(self, statement):
            statements.append(str(statement))
            value = isolation if str(statement) == "SHOW transaction_isolation" else read_only
            return SimpleNamespace(scalar_one=lambda: value)

    with pytest.raises(ResearchDataError) as raised:
        await snapshots._verify_transaction_settings(PostgreSQLSettings(), "postgresql")
    assert raised.value.code == "snapshot_transaction_unsafe"
    assert raised.value.status == 503
    assert statements == ["SHOW transaction_isolation", "SHOW transaction_read_only"]


@pytest.mark.asyncio
async def test_postgresql_manifest_settings_are_derived_from_show_results():
    snapshots = snapshot_module()
    assert hasattr(snapshots, "_verify_transaction_settings")
    statements = []

    class PostgreSQLSettings:
        async def execute(self, statement):
            statements.append(str(statement))
            value = "repeatable read" if str(statement) == "SHOW transaction_isolation" else "on"
            return SimpleNamespace(scalar_one=lambda: value)

    settings = await snapshots._verify_transaction_settings(PostgreSQLSettings(), "postgresql")
    assert statements == ["SHOW transaction_isolation", "SHOW transaction_read_only"]
    assert settings == {
        "isolation_level": "repeatable read",
        "read_only": True,
        "verification": "postgresql_show_transaction_settings",
    }


@pytest.mark.asyncio
async def test_sqlite_manifest_does_not_claim_postgresql_read_only_enforcement():
    snapshots = snapshot_module()
    async with snapshot_database() as (factory, database):
        _, manifest = await snapshots.load_versioned_market_data(factory, date(2024, 1, 2), date(2024, 1, 3))
    assert manifest.get("transaction") == {
        "isolation_level": "serializable",
        "read_only": False,
        "verification": "sqlite_explicit_transaction_select_only",
    }
