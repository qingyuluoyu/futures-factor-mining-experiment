from contextlib import asynccontextmanager
from datetime import date, datetime
from types import SimpleNamespace

import httpx
import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.stock_factors import AnalysisBody, BacktestBody, router
from app.core.deps import AuthContext, get_auth_context, get_session
from app.models.kline_history import KlineHistory
from app.models.stock import StockFinancial, StockInstrument, StockValuation


@pytest.mark.parametrize("fields", [
    {"rebalance": "invalid"},
    {"weight": "invalid"},
    {"factor_ids": []},
    {"factor_ids": ["roe", "roe"]},
    {"factor_ids": ["unknown"]},
    {"start_date": "garbage"},
    {"start_date": "20240230"},
    {"start_date": ""},
    {"end_date": "bad"},
    {"start_date": "2024-04-01", "end_date": "2024-01-01"},
    {"preprocess": {"winsorize": "unknown"}},
    {"commission_rate": float("nan")},
])
def test_request_schema_rejects_invalid_parameters(fields):
    with pytest.raises(ValidationError):
        BacktestBody(**fields)


@pytest.mark.parametrize("start", ["20240101", "2024-01-01", "2024/01/01"])
def test_legacy_date_formats_are_accepted_explicitly(start):
    assert BacktestBody(start_date=start, end_date="2024-04-30").start_date == start


def test_analysis_schema_rejects_unknown_factor_and_invalid_date():
    with pytest.raises(ValidationError):
        AnalysisBody(factor_id="unknown")
    with pytest.raises(ValidationError):
        AnalysisBody(factor_id="roe", end_date="tomorrow")


@asynccontextmanager
async def research_database(*, missing_held=False, with_prices=True):
    database = create_engine("sqlite:///:memory:")
    for model in (KlineHistory, StockInstrument, StockValuation, StockFinancial):
        model.__table__.create(database)
    with Session(database) as session:
        for position in range(10):
            code = str(600001 + position)
            session.add(StockInstrument(code=code, name=f"测试{position}", exchange="SSE", industry="测试行业", updated_at=datetime(2024, 1, 1)))
            if not with_prices:
                continue
            for offset, trading_date in enumerate(pd.bdate_range("2024-01-01", "2024-04-30")):
                if missing_held and code == "600010" and trading_date.date() == date(2024, 2, 15):
                    continue
                close = 100 * (1.001 + position * 0.001) ** offset
                session.add(KlineHistory(market_type="stock", code=code, trade_date=trading_date.date(), open=close, high=close, low=close, close=close, volume=10000))
                session.add(StockValuation(code=code, trade_date=trading_date.date(), pb=float(position + 1)))
        session.commit()

    class DatabaseSession:
        async def __aenter__(self):
            self.session = Session(database)
            return self

        async def __aexit__(self, *args):
            self.session.close()

        async def execute(self, statement):
            return self.session.execute(statement)

    application = FastAPI()
    application.include_router(router)
    application.state.session_factory = DatabaseSession
    application.dependency_overrides[get_session] = lambda: object()
    application.dependency_overrides[get_auth_context] = lambda: AuthContext(SimpleNamespace(id="research-qa", market_type="stock"), "virtual")
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application, raise_app_exceptions=False), base_url="http://test") as client:
            yield client
    finally:
        database.dispose()


@pytest.mark.asyncio
async def test_api_preserves_success_fields_and_runs_the_real_local_data_chain():
    async with research_database() as client:
        result = await client.post("/api/stock/backtest", json={"start_date": "20240101", "end_date": "20240430", "top_n": 1, "commission_rate": 0, "slippage_rate": 0})
        assert result.status_code == 200, result.text
        payload = result.json()
        assert {"nav", "trades", "metrics", "params", "rebalance_count"} <= payload.keys()
        assert payload["nav"][-1]["nav"] == pytest.approx(1.01 ** 64, abs=1e-7)
        assert payload["method_version"]
        assert result.headers["X-Request-ID"]
        analysis = await client.post("/api/stock/factors/analysis", json={"factor_id": "pb", "start_date": "20240101", "end_date": "20240430", "groups": 2})
        assert analysis.status_code == 200, analysis.text
        expected = 1 + np.mean([0.001, 0.002, 0.003, 0.004, 0.005]) - np.mean([0.006, 0.007, 0.008, 0.009, 0.010])
        assert analysis.json()["long_short_nav"][0]["nav"] == pytest.approx(expected, abs=1e-9)


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint,payload", [
    ("/api/stock/backtest", {"factor_ids": ["ret_20d", "roe"], "top_n": 1}),
    ("/api/stock/factors/analysis", {"factor_id": "roe"}),
])
async def test_api_missing_factor_has_explicit_non_200_error(endpoint, payload):
    async with research_database() as client:
        result = await client.post(endpoint, json={"start_date": "20240101", "end_date": "20240430", **payload})
    assert result.status_code == 404, result.text
    assert "roe" in result.json()["detail"]
    assert result.headers["X-Research-Error"] == "missing_factor_data"


@pytest.mark.asyncio
async def test_api_missing_held_price_is_a_data_conflict_not_500():
    async with research_database(missing_held=True) as client:
        result = await client.post("/api/stock/backtest", json={"start_date": "20240101", "end_date": "20240430", "top_n": 1})
    assert result.status_code == 409, result.text
    assert result.headers["X-Research-Error"] == "missing_held_price"


@pytest.mark.asyncio
async def test_api_empty_market_returns_404():
    async with research_database(with_prices=False) as client:
        result = await client.post("/api/stock/backtest", json={"top_n": 1})
    assert result.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{"rebalance": "bad"}, {"factor_ids": ["unknown"]}, {"start_date": "bad"}])
async def test_api_invalid_input_returns_422_before_loading_data(payload):
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_session] = lambda: object()
    application.dependency_overrides[get_auth_context] = lambda: AuthContext(SimpleNamespace(id="research-qa", market_type="stock"), "virtual")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application, raise_app_exceptions=False), base_url="http://test") as client:
        result = await client.post("/api/stock/backtest", json=payload)
    assert result.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint,payload", [("/api/stock/backtest", {}), ("/api/stock/factors/analysis", {"factor_id": "pb"})])
async def test_research_endpoints_require_authentication(endpoint, payload):
    application = FastAPI()
    application.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        result = await client.post(endpoint, json=payload)
    assert result.status_code == 401
