import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.stock import router
from app.core.deps import get_current_user, get_session, get_settings
from app.models.stock import StockFinancial, StockValuation


@pytest.fixture
def query_application():
    engine = create_engine("sqlite:///:memory:")
    StockValuation.__table__.create(engine)
    StockFinancial.__table__.create(engine)
    statements = []
    with Session(engine) as session:
        session.add_all([
            StockValuation(
                code="600519", trade_date=date(2018, 1, 1) + timedelta(days=index),
                pe_ttm=None if index == 3004 else float(index), pb=1.5,
                total_mv=10.0, circ_mv=None, turnover_rate=2.0,
            )
            for index in range(3005)
        ])
        session.add(StockValuation(code="000001", trade_date=date(2026, 10, 16), pe_ttm=999.0))
        session.add_all([
            StockFinancial(
                code="600519", report_date=date(2015 + index // 4, (index % 4 + 1) * 3, 28),
                publish_date=None if index == 44 else date(2015 + index // 4, (index % 4 + 1) * 3, 28) + timedelta(days=30),
                roe=10.0, gross_margin=None, revenue_yoy=20.0, netprofit_yoy=-5.0,
                eps=1.5, bps=2.0, netprofit=100.0,
                updated_at=datetime(2026, 10, 19),
            )
            for index in range(45)
        ])
        session.commit()

    class QuerySession:
        async def execute(self, statement):
            statements.append(statement)
            return session.execute(statement)

    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="query-owner", market_type="stock")
    application.dependency_overrides[get_settings] = lambda: object()
    with Session(engine) as session:
        application.dependency_overrides[get_session] = lambda: QuerySession()
        yield application, statements
    engine.dispose()


@pytest.mark.parametrize("endpoint", ["valuation", "financials"])
@pytest.mark.parametrize("code", ["６００５１９", "٦٠٠٥١٩", "60051", "6005190", "600519\n", "600519.SH", "rb2701"])
async def test_stock_query_rejects_non_ascii_or_non_six_digit_codes(query_application, endpoint, code):
    application, statements = query_application
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        response = await client.get(f"/api/stock/{endpoint}", params={"code": code})
    assert response.status_code == 422
    assert statements == []


@pytest.mark.parametrize("field", ["start", "end"])
@pytest.mark.parametrize("value", ["not-a-date", "2026-02-30", "20261016", "2026-W42-5", "2026-10-16T00:00:00", "2026-1-1", " 2026-10-16", "２０２６-１０-１６"])
async def test_valuation_rejects_invalid_calendar_or_non_iso_dates(query_application, field, value):
    application, statements = query_application
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        response = await client.get("/api/stock/valuation", params={"code": "600519", field: value})
    assert response.status_code == 422
    assert statements == []


async def test_valuation_rejects_reversed_dates_before_query(query_application):
    application, statements = query_application
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        response = await client.get("/api/stock/valuation?code=600519&start=2026-10-16&end=2026-10-15")
    assert response.status_code == 422
    assert statements == []


@pytest.mark.parametrize("endpoint,query", [
    ("valuation", "limit=0"), ("valuation", "limit=3001"),
    ("financials", "limit=0"), ("financials", "limit=201"),
    ("valuation", "offset=-1"), ("financials", "offset=-1"),
    ("valuation", "offset=100001"), ("financials", "offset=100001"),
])
async def test_stock_query_rejects_out_of_bounds_pagination(query_application, endpoint, query):
    application, statements = query_application
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        response = await client.get(f"/api/stock/{endpoint}?code=600519&{query}")
    assert response.status_code == 422
    assert statements == []


async def test_valuation_default_covers_latest_3000_and_preserves_ascending_array_contract(query_application):
    application, statements = query_application
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        response = await client.get("/api/stock/valuation?code=600519&start=&end=")
    assert response.status_code == 200
    rows = response.json()
    assert isinstance(rows, list) and len(rows) == 3000
    assert rows[0]["trade_date"] == (date(2018, 1, 1) + timedelta(days=5)).isoformat()
    assert rows[-1] == {
        "trade_date": (date(2018, 1, 1) + timedelta(days=3004)).isoformat(),
        "pe_ttm": None, "pb": 1.5, "total_mv": 10.0, "circ_mv": None, "turnover_rate": 2.0,
    }
    assert response.headers["X-Page-Has-More"] == "true"
    assert response.headers["X-Page-Limit"] == "3000"
    assert response.headers["X-Page-Order"] == "trade_date_desc"
    assert response.headers["X-Result-Order"] == "trade_date_asc"
    assert statements[0]._limit_clause.value == 3001


async def test_valuation_pages_cover_inclusive_date_range_without_duplicates(query_application):
    application, statements = query_application
    responses = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        for offset in (0, 2, 4):
            response = await client.get("/api/stock/valuation", params={"code": "600519", "start": "2018-01-02", "end": "2018-01-06", "limit": 2, "offset": offset})
            assert response.status_code == 200
            responses.append(response)
    pages = [response.json() for response in responses]
    assert [[row["trade_date"] for row in page] for page in pages] == [
        ["2018-01-05", "2018-01-06"], ["2018-01-03", "2018-01-04"], ["2018-01-02"],
    ]
    assert len({row["trade_date"] for page in pages for row in page}) == 5
    assert [response.headers["X-Page-Has-More"] for response in responses] == ["true", "true", "false"]
    assert [response.headers["X-Page-Offset"] for response in responses] == ["0", "2", "4"]
    assert [response.headers["X-Page-Returned"] for response in responses] == ["2", "2", "1"]
    assert all(statement._limit_clause.value == 3 for statement in statements)


async def test_financial_default_40_and_pages_preserve_report_and_disclosure_semantics(query_application):
    application, statements = query_application
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        default = await client.get("/api/stock/financials?code=600519")
        page = await client.get("/api/stock/financials?code=600519&limit=5&offset=40")
    assert default.status_code == page.status_code == 200
    rows = default.json()
    assert len(rows) == 40 and len(page.json()) == 5
    assert len({row["report_date"] for row in rows + page.json()}) == 45
    assert rows[0] == {
        "report_date": "2026-03-28", "publish_date": None,
        "roe": 10.0, "gross_margin": None, "revenue_yoy": 20.0, "netprofit_yoy": -5.0,
        "eps": 1.5, "bps": 2.0, "netprofit": 100.0,
    }
    assert default.headers["X-Page-Has-More"] == "true"
    assert page.headers["X-Page-Has-More"] == "false"
    assert default.headers["X-Page-Limit"] == "40"
    assert default.headers["X-Page-Order"] == "report_date_desc"
    assert default.headers["X-Result-Order"] == "report_date_desc"
    assert [statement._limit_clause.value for statement in statements] == [41, 6]


@pytest.mark.parametrize("endpoint,frequency", [("valuation", "daily"), ("financials", "quarterly")])
async def test_query_headers_disclose_declared_units_and_unverified_legacy_provenance(query_application, endpoint, frequency):
    application, _ = query_application
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        response = await client.get(f"/api/stock/{endpoint}?code=600519&limit=1")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Request-ID"]
    metadata = json.loads(response.headers["X-Data-Metadata"])
    assert metadata["market"] == "CN-A"
    assert metadata["currency"] == "CNY"
    assert metadata["timezone"] == "Asia/Shanghai"
    assert metadata["frequency"] == frequency
    assert metadata["source"] == "local_import"
    assert metadata["supplier"] == "unknown"
    assert metadata["provenance"] == "legacy_unverified"
    assert metadata["unit_status"] == "schema_declared_not_row_verified"
    assert metadata["license"] == "unverified"
    assert metadata["data_version"] is None
    if endpoint == "valuation":
        assert metadata["units"]["total_mv"] == "CNY_100_million"
        assert metadata["units"]["turnover_rate"] == "percent"
        assert metadata["date_semantics"]["trade_date"] == "trading_date"
    else:
        assert metadata["units"]["eps"] == "CNY_per_share"
        assert metadata["date_semantics"]["report_date"] == "report_period_end"
        assert metadata["date_semantics"]["publish_date"] == "disclosure_date_nullable_unverified"
        assert metadata["point_in_time_certified"] is False


@pytest.mark.parametrize("endpoint", ["valuation", "financials"])
async def test_stock_queries_empty_result_and_offset_remain_explicit(query_application, endpoint):
    application, _ = query_application
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        response = await client.get(f"/api/stock/{endpoint}?code=600519&limit=1&offset=100000")
    assert response.status_code == 200 and response.json() == []
    assert response.headers["X-Page-Returned"] == "0"
    assert response.headers["X-Page-Has-More"] == "false"


@pytest.mark.parametrize("endpoint", ["valuation", "financials"])
async def test_stock_queries_require_authentication_before_reading(query_application, endpoint):
    application, statements = query_application
    del application.dependency_overrides[get_current_user]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        response = await client.get(f"/api/stock/{endpoint}?code=600519")
    assert response.status_code == 401
    assert statements == []


@pytest.mark.parametrize("endpoint", ["valuation", "financials"])
async def test_stock_query_failures_are_traceable_unavailable_errors_not_empty_data(query_application, endpoint):
    application, _ = query_application

    class FailingSession:
        async def execute(self, statement):
            raise RuntimeError("database unavailable")

    application.dependency_overrides[get_session] = lambda: FailingSession()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application, raise_app_exceptions=False), base_url="http://test") as client:
        response = await client.get(f"/api/stock/{endpoint}?code=600519")
    assert response.status_code == 503
    assert response.headers["X-Request-ID"]
    assert response.headers["Cache-Control"] == "no-store"
    assert "database unavailable" not in response.text
