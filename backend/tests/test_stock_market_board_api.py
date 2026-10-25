from datetime import date, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.stock_market import router
from app.api.stock import router as stock_router
from app.core.deps import get_current_user, get_session
from app.models.kline_history import KlineHistory
from app.models.stock import StockFinancial, StockInstrument, StockValuation


@pytest.fixture
def market_app():
    database = create_engine("sqlite:///:memory:")
    for model in (KlineHistory, StockInstrument, StockValuation, StockFinancial):
        model.__table__.create(database)
    today = date.today()
    with Session(database) as session:
        session.add_all([
            StockInstrument(code="600001", name="测试股票", exchange="SSE", industry="测试", updated_at=datetime.now()),
            StockInstrument(code="000001", name="目录股票", exchange="SZSE", industry="银行", updated_at=datetime.now()),
        ])
        for trading_date, price in [(today - timedelta(days=1), 10), (today, 11), (today + timedelta(days=1), 100)]:
            session.add(KlineHistory(code="600001", market_type="stock", trade_date=trading_date, open=price, high=price, low=price, close=price, volume=100))
        session.add(KlineHistory(code="IF2606", market_type="futures", trade_date=today, open=5000, high=5000, low=5000, close=5000, volume=100))
        session.add_all([
            StockFinancial(code="600001", report_date=today - timedelta(days=90), publish_date=today, eps=1),
            StockFinancial(code="600001", report_date=today, publish_date=today + timedelta(days=1), eps=99),
            StockFinancial(code="600001", report_date=today - timedelta(days=180), publish_date=None, eps=99),
            StockValuation(code="600001", trade_date=today, pe_ttm=12, total_mv=100),
        ])
        session.commit()

    class AsyncSessionAdapter:
        def __init__(self, session):
            self.session = session

        async def execute(self, statement):
            return self.session.execute(statement)

        async def scalar(self, statement):
            return self.session.scalar(statement)

    async def session_dependency():
        with Session(database) as session:
            yield AsyncSessionAdapter(session)

    application = FastAPI()
    application.include_router(router)
    application.include_router(stock_router)
    application.dependency_overrides[get_session] = session_dependency
    application.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="qa-market", market_type="stock")
    yield application
    database.dispose()


@pytest.mark.asyncio
async def test_market_sql_api_filters_market_future_dates_and_missing_quotes(market_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=market_app), base_url="http://test") as client:
        boards = await client.get("/api/stock/market/boards")
        assert boards.status_code == 200
        assert boards.headers["X-Request-ID"]
        assert boards.json()["coverage"] == {"instruments": 2, "quoted": 1, "comparable": 1}
        row = next(item for item in boards.json()["boards"] if item["industry"] == "测试")
        assert row["change_pct"] == pytest.approx(10)
        members = (await client.get("/api/stock/market/members?data_only=true&limit=1")).json()
        assert members["total"] == 1 and members["items"][0]["close"] == 11
        missing = (await client.get("/api/stock/market/members?keyword=目录")).json()["items"][0]
        assert missing["close"] is None and not missing["data_available"]
        detail_response = await client.get("/api/stock/market/details?code=600001")
        assert detail_response.headers.get("X-Request-ID")
        details = detail_response.json()
        assert details["coverage"]["daily_rows"] == 2
        assert len(details["financials"]) == 1 and details["financials"][0]["eps"] == 1
        assert details["valuation"]["total_mv"] == 100
        chart = (await client.get("/api/stock/kline?code=600001")).json()
        assert len(chart["bars"]) == 2 and chart["bars"][-1]["close"] == 11
        assert (await client.get("/api/stock/market/details?code=600099")).status_code == 404


@pytest.mark.asyncio
async def test_market_api_bounded_inputs_and_authentication(market_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=market_app), base_url="http://test") as client:
        for path in ["members?limit=101", "members?sort=profit", "members?offset=10001", "boards?limit=201", "details?code=IF2606"]:
            assert (await client.get("/api/stock/market/" + path)).status_code == 422
        market_app.dependency_overrides.pop(get_current_user)
        assert (await client.get("/api/stock/market/boards")).status_code in (401, 403)


@pytest.mark.asyncio
async def test_market_database_failure_is_not_disguised_as_empty_data(market_app):
    class BrokenSession:
        async def execute(self, statement):
            raise RuntimeError("private-database-error")

        async def scalar(self, statement):
            raise RuntimeError("private-database-error")

    market_app.dependency_overrides[get_session] = lambda: BrokenSession()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=market_app), base_url="http://test") as client:
        for path in ("boards", "members", "details?code=600001"):
            response = await client.get("/api/stock/market/" + path)
            assert response.status_code == 503 and response.headers.get("X-Request-ID")
            assert "private-database-error" not in response.text
