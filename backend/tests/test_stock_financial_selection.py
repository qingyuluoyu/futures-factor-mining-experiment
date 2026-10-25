from contextlib import asynccontextmanager
from datetime import date, datetime
import importlib
import importlib.util

import pandas as pd
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.models.kline_history import KlineHistory
from app.models.stock import StockFinancial, StockInstrument, StockValuation
from app.services.stock_factors.engine import METHOD_VERSION, ResearchDataError, _build_market_data, load_market_data
from app.services.stock_factors.snapshots import load_versioned_market_data


def financial_rows():
    return [
        ("600001", date(2023, 12, 31), date(2024, 4, 30), 10.0, 20.0, 30.0, 40.0),
        ("600001", date(2024, 3, 31), date(2024, 4, 30), None, 21.0, 31.0, 41.0),
        ("600001", date(2023, 9, 30), date(2024, 5, 2), 99.0, 99.0, 99.0, 99.0),
        ("600001", date(2023, 6, 30), date(2024, 4, 29), 7.0, 17.0, 27.0, 37.0),
    ]


def market_for(rows):
    return _build_market_data(
        [("600001", date(2024, 4, 29), 10.0)]
        + [("600001", date(2024, month, day), 10.0) for month, day in ((4, 30), (5, 1), (5, 2), (5, 3))],
        [], rows, [],
    )


def selector_module():
    assert importlib.util.find_spec("app.services.stock_factors.financial_selection") is not None
    return importlib.import_module("app.services.stock_factors.financial_selection")


@pytest.mark.parametrize("reverse", [False, True])
def test_panel_keeps_latest_report_frontier_with_unordered_disclosures(reverse):
    rows = financial_rows()
    market = market_for(list(reversed(rows)) if reverse else rows)
    assert pd.isna(market.financial["roe"].loc["2024-04-29", "600001"])
    assert market.financial["roe"].loc["2024-04-30", "600001"] == 7.0
    for observed_date in ("2024-05-01", "2024-05-02", "2024-05-03"):
        assert pd.isna(market.financial["roe"].loc[observed_date, "600001"])
        assert market.financial["gross_margin"].loc[observed_date, "600001"] == 21.0
        assert market.financial["revenue_yoy"].loc[observed_date, "600001"] == 31.0
        assert market.financial["netprofit_yoy"].loc[observed_date, "600001"] == 41.0


def test_selector_sorts_without_mutating_input_and_maintains_independent_frontiers():
    module = selector_module()
    rows = financial_rows() + [("600002", date(2023, 12, 31), date(2024, 4, 29), 5.0, None, None, None)]
    original = list(rows)
    expected = [rows[3], rows[1], rows[4]]
    assert module.select_financial_rows(rows) == expected
    assert rows == original
    assert module.select_financial_rows(list(reversed(rows))) == expected
    assert module.FINANCIAL_SELECTION_VERSION == "latest-disclosed-report-frontier-v1"


def test_missing_announcement_is_unavailable_and_empty_market_remains_compatible():
    unknown = ("600001", date(2024, 3, 31), None, 100.0, 100.0, 100.0, 100.0)
    market = market_for([unknown])
    assert market.financial == {}
    assert selector_module().select_financial_rows([unknown]) == []
    empty = _build_market_data([], [], [unknown], [])
    assert empty.close.empty
    assert empty.financial == {}


@pytest.mark.parametrize("change", [None, "value", "announcement"])
def test_panel_rejects_duplicate_or_conflicting_security_report_period(change):
    row = financial_rows()[0]
    duplicate = list(row)
    if change == "value":
        duplicate[3] = 999.0
    elif change == "announcement":
        duplicate[2] = date(2024, 5, 1)
    with pytest.raises(ResearchDataError) as raised:
        market_for([row, tuple(duplicate)])
    assert raised.value.code == "invalid_market_data"


def test_method_version_records_financial_selection_change():
    assert METHOD_VERSION == "stock-research-2026-10-19.v5"


@pytest.mark.parametrize("position,value", [
    (0, "AU"),
    (1, "2023-12-31"),
    (2, "2024-04-30"),
    (1, date(2024, 5, 1)),
    (3, float("inf")),
    (4, float("nan")),
    (5, True),
    (6, "40"),
])
def test_invalid_raw_disclosure_is_rejected_before_selection(position, value):
    rows = financial_rows()
    invalid = list(rows[0])
    invalid[position] = value
    rows[0] = tuple(invalid)
    with pytest.raises(ResearchDataError) as raised:
        market_for(rows)
    assert raised.value.code == "invalid_market_data"
    with pytest.raises(selector_module().FinancialSelectionError):
        selector_module().select_financial_rows(rows)


def test_selector_rejects_incomplete_contract_and_accepts_empty_input():
    module = selector_module()
    assert module.select_financial_rows([]) == []
    with pytest.raises(module.FinancialSelectionError):
        module.select_financial_rows([financial_rows()[0][:-1]])


@asynccontextmanager
async def financial_database():
    database = create_engine("sqlite:///:memory:")
    for model in (KlineHistory, StockInstrument, StockValuation, StockFinancial):
        model.__table__.create(database)
    with Session(database) as session:
        session.add(StockInstrument(code="600001", name="测试证券", exchange="SSE", industry="金融", updated_at=datetime(2024, 1, 1)))
        for month, day in ((4, 29), (4, 30), (5, 1), (5, 2), (5, 3)):
            session.add(KlineHistory(market_type="stock", code="600001", trade_date=date(2024, month, day), open=10, high=10, low=10, close=10, volume=100))
        session.add(KlineHistory(market_type="futures", code="AU", trade_date=date(2024, 5, 3), open=100, high=100, low=100, close=100, volume=100))
        for code, report, published, roe, margin, revenue, profit in financial_rows():
            session.add(StockFinancial(code=code, report_date=report, publish_date=published, roe=roe, gross_margin=margin, revenue_yoy=revenue, netprofit_yoy=profit, updated_at=datetime(2024, 1, 1)))
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
async def test_real_sql_legacy_and_snapshot_financial_panels_match_and_keep_raw_fingerprint():
    async with financial_database() as (factory, database):
        legacy = await load_market_data(factory, date(2024, 4, 29), date(2024, 5, 3))
        snapshot, before = await load_versioned_market_data(factory, date(2024, 4, 29), date(2024, 5, 3))
        assert list(legacy.close.columns) == ["600001"]
        pd.testing.assert_frame_equal(legacy.close, snapshot.close)
        for factor in ("roe", "gross_margin", "revenue_yoy", "netprofit_yoy"):
            pd.testing.assert_frame_equal(legacy.financial[factor], snapshot.financial[factor])
        assert before["financial_rows"] == {"raw": 4, "selected": 2}
        with database.begin() as connection:
            connection.execute(text("UPDATE stock_financial SET roe = 101 WHERE report_date = '2023-09-30'"))
        updated, after = await load_versioned_market_data(factory, date(2024, 4, 29), date(2024, 5, 3))
        assert before["data_version"] != after["data_version"]
        pd.testing.assert_frame_equal(snapshot.financial["roe"], updated.financial["roe"])


@pytest.mark.asyncio
async def test_legacy_no_price_result_stays_empty_and_snapshot_reports_missing_data():
    async with financial_database() as (factory, database):
        legacy = await load_market_data(factory, date(2020, 1, 1), date(2020, 1, 2))
        assert legacy.close.empty
        assert legacy.financial == {}
        with pytest.raises(ResearchDataError) as raised:
            await load_versioned_market_data(factory, date(2020, 1, 1), date(2020, 1, 2))
        assert raised.value.code == "missing_market_data"
        assert raised.value.status == 404
