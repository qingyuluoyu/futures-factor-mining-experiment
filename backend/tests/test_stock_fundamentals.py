from datetime import date
import importlib
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import event, text

from app.core.deps import get_auth_context, get_current_user, AuthContext
from app.services.stock_factors.engine import ResearchDataError
from tests.test_stock_research_snapshots import snapshot_database


def modules():
    return importlib.import_module("app.schemas.stock_fundamentals"), importlib.import_module("app.services.stock_fundamentals")


@pytest.mark.parametrize("patch", [{"code": "６００００１"}, {"code": "AU"}, {"as_of": "20240103"}, {"as_of": "2024-02-30"}, {"limit": 101}, {"offset": -1}, {"kind": "orders"}, {"extra": "unsafe"}])
def test_history_query_is_strict_and_bounded(patch):
    schema, _ = modules()
    with pytest.raises(ValidationError):
        schema.FundamentalQuery(**{"code": "600001", "as_of": "2024-01-03", "kind": "financial", **patch})


@pytest.mark.asyncio
async def test_disclosure_classification_page_union_and_same_day_display():
    schema, service = modules()
    async with snapshot_database() as (factory, database):
        with database.begin() as connection:
            connection.execute(text("UPDATE stock_financial SET publish_date='2024-01-03', roe=-12 WHERE code='600001' AND report_date='2023-09-30'"))
            connection.execute(text("INSERT INTO stock_financial (id,code,report_date,publish_date,roe,updated_at) VALUES (95001,'600001','2023-06-30',NULL,NULL,'2024-01-01'), (95002,'600001','2023-03-31','2023-03-01',99,'2024-01-01'), (95003,'600001','2022-12-31','2023-04-30',1,'2024-01-01')"))
        reads = []
        event.listen(database, "before_cursor_execute", lambda connection, cursor, statement, parameters, context, many: reads.append(statement))
        query = schema.FundamentalQuery(code="600001", kind="financial", as_of="2024-01-03", limit=1)
        first = await service.read_fundamentals(factory, query, today=date(2024, 1, 3))
        second = await service.read_fundamentals(factory, query.model_copy(update={"offset": 1}), today=date(2024, 1, 3))
    assert first["counts"] == {"source_rows": 5, "eligible_rows": 2, "unknown_publication": 1, "invalid_dates": 1, "later_rows": 1}
    assert first["instrument"]["instrument_id"] == "600001.SSE"
    assert first["rows"][0]["publish_date"] == "2024-01-03"
    assert first["rows"][0]["roe"] == -12
    assert first["rows"][0]["gross_margin"] is None
    assert first["has_more"] is True and second["has_more"] is False
    assert [first["rows"][0]["report_date"], second["rows"][0]["report_date"]] == ["2023-09-30", "2022-12-31"]
    assert first["point_in_time_certified"] is False and first["source"] == "local_import"
    assert first["data_version"] is None and first["provenance"] == "legacy_unverified"
    assert first["supplier"] == "unknown" and first["license_scope"] == "unverified"
    assert first["collected_at"] is None and first["trading_calendar"] == "unverified"
    assert first["frequency"] == "quarterly" and first["financial_basis"] == "unverified"
    assert first["price_adjustment"] == "not_applicable" and first["unit_status"] == "schema_declared_not_row_verified"
    assert first["date_semantics"] == {"report_date": "report_period_end", "publish_date": "disclosure_date_inclusive_display_only"}
    assert len([statement for statement in reads if statement.lstrip().upper().startswith("SELECT")]) == 6
    assert not any(statement.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE")) for statement in reads)


@pytest.mark.asyncio
async def test_valuation_history_keeps_negative_and_null_values_and_does_not_mix_stocks():
    schema, service = modules()
    async with snapshot_database() as (factory, _):
        result = await service.read_fundamentals(factory, schema.FundamentalQuery(code="600001", kind="valuation", as_of="2024-01-02"), today=date(2024, 1, 3))
    assert result["counts"] == {"source_rows": 2, "eligible_rows": 1, "unknown_publication": 0, "invalid_dates": 0, "later_rows": 1}
    assert result["rows"][0]["trade_date"] == "2024-01-02"
    assert result["rows"][0]["pe_ttm"] == -10 and result["rows"][0]["pb"] is None
    assert result["units"]["total_mv"] == "CNY_100_million"


@pytest.mark.asyncio
async def test_future_unknown_exchange_and_source_capacity_fail_explicitly(monkeypatch):
    schema, service = modules()
    async with snapshot_database() as (factory, database):
        query = schema.FundamentalQuery(code="600001", kind="financial", as_of="2024-01-04")
        with pytest.raises(ResearchDataError) as error:
            await service.read_fundamentals(factory, query, today=date(2024, 1, 3))
        assert error.value.status == 422
        with pytest.raises(ResearchDataError) as error:
            await service.read_fundamentals(factory, query.model_copy(update={"code": "600999", "as_of": date(2024, 1, 3)}), today=date(2024, 1, 3))
        assert error.value.status == 404
        monkeypatch.setattr(service, "MAX_SOURCE_ROWS", 1)
        with pytest.raises(ResearchDataError) as error:
            await service.read_fundamentals(factory, query.model_copy(update={"as_of": date(2024, 1, 3)}), today=date(2024, 1, 3))
        assert error.value.code == "fundamental_limit_exceeded"
        monkeypatch.setattr(service, "MAX_SOURCE_ROWS", 50000)
        with database.begin() as connection:
            connection.execute(text("UPDATE stock_instruments SET exchange='UNKNOWN' WHERE code='600001'"))
        with pytest.raises(ResearchDataError) as error:
            await service.read_fundamentals(factory, query.model_copy(update={"as_of": date(2024, 1, 3)}), today=date(2024, 1, 3))
        assert error.value.status == 409


@pytest.mark.asyncio
async def test_nonfinite_stored_values_are_errors_not_null_or_dropped_rows():
    schema, service = modules()
    async with snapshot_database() as (factory, database):
        with database.begin() as connection:
            connection.execute(text("UPDATE stock_valuation SET pe_ttm=1e999 WHERE code='600001'"))
        with pytest.raises(ResearchDataError) as error:
            await service.read_fundamentals(factory, schema.FundamentalQuery(code="600001", kind="valuation", as_of="2024-01-03"), today=date(2024, 1, 3))
        assert error.value.code == "invalid_fundamental_values"


@pytest.mark.asyncio
async def test_new_route_inherits_stock_gate_and_all_responses_trace_and_no_store():
    modules()
    router = importlib.import_module("app.api.stock_market").router
    async with snapshot_database() as (factory, _):
        app = FastAPI()
        app.include_router(router)
        app.state.session_factory = factory
        user = SimpleNamespace(id="owner", market_type="stock")
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_auth_context] = lambda: AuthContext(user=user, trading_mode="virtual")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            url = "/api/stock/market/fundamentals?code=600001&as_of=2024-01-03&kind=financial"
            response = await client.get(url)
            assert response.status_code == 200
            assert response.json()["transaction"] == "sqlite_explicit_select_only"
            user.market_type = "futures"
            denied = await client.get(url)
            assert denied.status_code == 409
            user.market_type = "stock"
            invalid = await client.get(url + "&extra=unsafe")
            assert invalid.status_code == 422
            for item in (response, denied, invalid):
                assert item.headers["Cache-Control"] == "no-store"
                assert len(item.headers["X-Request-ID"]) == 32
