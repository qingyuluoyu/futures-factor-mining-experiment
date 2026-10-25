from datetime import date
from importlib import import_module

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.models.stock import StockFinancial, StockValuation
from app.models.kline_history import KlineHistory
from tests.test_stock_research_snapshots import snapshot_database


def query(**changes):
    module = import_module("app.schemas.stock_research_inputs")
    return module.ResearchInputQuery(**{"start_date": "2024-01-02", "end_date": "2024-01-03", "factor_ids": ["pe_ttm", "pb", "roe", "gross_margin", "ret_20d"], **changes})


@pytest.mark.parametrize("changes", [{"factor_ids": []}, {"factor_ids": ["roe", "roe"]}, {"factor_ids": ["unknown"]},
    {"start_date": "20240102"}, {"end_date": "2024-01-01"}, {"extra": 1}])
def test_input_query_is_strict_and_bounded(changes):
    with pytest.raises(ValidationError):
        query(**changes)


def test_input_factor_identifier_has_its_own_length_boundary():
    with pytest.raises(ValidationError) as error:
        query(factor_ids=["x" * 41])
    assert error.value.errors()[0]["type"] == "string_too_long"


@pytest.mark.asyncio
async def test_input_counts_separate_source_rows_domain_and_disclosure_without_PIT_claims():
    module = import_module("app.services.stock_research_inputs")
    async with snapshot_database() as (factory, database):
        result = await module.read_research_inputs(factory, query(), today=date(2024, 1, 3))
    fields = {item["field"]: item for item in result["fields"]}
    factors = {item["factor_id"]: item for item in result["factors"]}
    assert result["prices"]["rows"] == 4 and result["prices"]["securities"] == 2
    assert fields["pe_ttm"]["finite_values"] == fields["pe_ttm"]["excluded_nonpositive"] == 4
    assert fields["pe_ttm"]["usable_values"] == 0 and fields["pb"]["usable_values"] == 2
    assert fields["roe"]["source_rows"] == fields["roe"]["usable_values"] == 2
    assert fields["gross_margin"]["missing_values"] == 2
    assert factors["pe_ttm"]["status"] == factors["gross_margin"]["status"] == factors["ret_20d"]["status"] == "missing_inputs"
    assert factors["pb"]["status"] == factors["roe"]["status"] == "potential_inputs"
    assert result["status"] == "missing_inputs" and result["point_in_time_certified"] is False
    assert result["source_calls"] == result["model_calls"] == 0


@pytest.mark.asyncio
async def test_input_valuation_requires_actual_price_date_and_financial_strictly_prior_publish():
    module = import_module("app.services.stock_research_inputs")
    async with snapshot_database() as (factory, database):
        with Session(database) as session:
            session.query(StockFinancial).delete()
            session.add(StockFinancial(code="600001", report_date=date(2023, 12, 31), publish_date=date(2024, 1, 3), roe=12))
            session.query(KlineHistory).filter(KlineHistory.code == "600002", KlineHistory.trade_date == date(2024, 1, 3)).delete()
            session.commit()
        result = await module.read_research_inputs(factory, query(factor_ids=["roe", "pb"]), today=date(2024, 1, 3))
    fields = {item["field"]: item for item in result["fields"]}
    assert fields["roe"]["source_rows"] == 0
    assert fields["pb"]["source_rows"] == 3 and fields["pb"]["usable_values"] == 1


@pytest.mark.asyncio
async def test_price_quality_and_market_cap_are_not_hidden_as_empty_inputs():
    module = import_module("app.services.stock_research_inputs")
    async with snapshot_database() as (factory, database):
        with Session(database) as session:
            session.query(StockValuation).update({StockValuation.total_mv: -1})
            session.query(KlineHistory).filter(KlineHistory.market_type == "stock", KlineHistory.code == "600001").update({KlineHistory.close: 0})
            session.commit()
        result = await module.read_research_inputs(factory, query(factor_ids=["pb"], mktcap_neutral=True), today=date(2024, 1, 3))
    assert result["status"] == "invalid_inputs" and result["prices"]["invalid_rows"] == 2
    assert next(item for item in result["fields"] if item["field"] == "total_mv")["usable_values"] == 0


@pytest.mark.asyncio
async def test_input_failure_future_period_and_capacity_do_not_return_zero(monkeypatch):
    module = import_module("app.services.stock_research_inputs")
    async with snapshot_database() as (factory, database):
        with pytest.raises(ValueError):
            await module.read_research_inputs(factory, query(), today=date(2024, 1, 2))
        monkeypatch.setattr(module, "MAX_PRICE_ROWS", 1)
        with pytest.raises(ValueError):
            await module.read_research_inputs(factory, query(), today=date(2024, 1, 3))
