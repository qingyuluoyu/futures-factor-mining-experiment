from datetime import date
from types import SimpleNamespace

from fastapi import FastAPI
import httpx
import pytest
from sqlalchemy import select

from app.api.stock import router
from app.core.deps import get_current_user, get_session
from app.models.kline_history import KlineHistory
from app.models.stock_eod import StockEodBatch
from app.services.stock_eod_sync import run_eod
from tests.test_stock_eod import catalog, row
from tests.test_stock_eod_integration import Source, database


@pytest.mark.asyncio
async def test_kline_sources_and_explicit_selection_preserve_legacy_without_cross_source_fallback():
    async with database() as factory:
        async with factory() as session:
            KlineHistory.__table__.create(session.get_bind())
        await run_eod(factory, Source((catalog(), [row(), row(ts_code="600002.SH")], False)), date(2026, 10, 16))
        async with factory() as session:
            session.add(KlineHistory(code="600001", market_type="stock", trade_date=date(2026, 10, 15), open=99, high=99, low=99, close=99, volume=1))
            await session.commit()
        app = FastAPI(); app.include_router(router); app.state.stock_eod_enabled = True
        async def dependency():
            async with factory() as session:
                yield session
        app.dependency_overrides[get_session] = dependency
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="qa-sources", market_type="stock")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            catalog_response = await client.get("/api/stock/kline/sources?code=600001")
            assert catalog_response.status_code == 200
            assert catalog_response.headers["X-Request-ID"] and catalog_response.headers["Cache-Control"] == "no-store"
            sources = catalog_response.json()
            assert sources["catalog_version"] == "stock-kline-sources-v1"
            indexed = {item["id"]: item for item in sources["sources"]}
            assert indexed["history_qfq"]["available_bars"] == indexed["eod_raw"]["available_bars"] == 1
            assert indexed["history_qfq"]["last_date"] == "2026-10-15"
            assert indexed["eod_raw"]["last_date"] == "2026-10-16"
            assert indexed["eod_raw"]["window_cap"] == 250
            legacy = await client.get("/api/stock/kline?code=600001")
            assert legacy.json()["source"] == "local_import"
            explicit = await client.get("/api/stock/kline?code=600001&source=eod_raw")
            assert explicit.json()["selected_source"] == "eod_raw"
            assert explicit.json()["source"] == "teajoin_eod" and explicit.json()["bars"][0]["close"] == 11
            assert (await client.get("/api/stock/kline?code=600002&source=history_qfq")).status_code == 409
            assert (await client.get("/api/stock/kline?code=600001&source=bad")).status_code == 422
            assert (await client.get("/api/stock/kline/sources?code=６００００１")).status_code == 422
            app.state.stock_eod_enabled = False
            disabled = await client.get("/api/stock/kline?code=600001&source=eod_raw")
            assert disabled.status_code == 503 and disabled.headers["X-Kline-Error"] == "source_disabled"
            disabled_catalog = (await client.get("/api/stock/kline/sources?code=600001")).json()
            disabled_item = next(item for item in disabled_catalog["sources"] if item["id"] == "eod_raw")
            assert disabled_item["reason"] == "eod_disabled" and disabled_item["available_bars"] is None
            assert disabled_item["available"] is False
            # An off EOD flag must not require or read its physical table.
            async with factory() as session:
                StockEodBatch.__table__.drop(session.get_bind())
            assert (await client.get("/api/stock/kline/sources?code=600001")).status_code == 200
            absent = (await client.get("/api/stock/kline/sources?code=699999")).json()
            assert all(item["reason"] == "instrument_not_found" and item["available_bars"] == 0 for item in absent["sources"])
            assert (await client.get("/api/stock/kline?code=699999&source=history_qfq")).status_code == 404
            app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="qa-futures", market_type="futures")
            denied = await client.get("/api/stock/kline/sources?code=600001")
            assert denied.status_code == 409 and denied.headers["X-Market-Capability"] == "stock_required"


@pytest.mark.asyncio
async def test_stock_kline_retains_vendor_ex_rights_reference_without_mixing_adjusted_history():
    async with database() as factory:
        async with factory() as session:
            KlineHistory.__table__.create(session.get_bind())
        await run_eod(factory, Source((catalog(), [row(pre_close=5, vol=0.01), row(ts_code="600002.SH")], False)), date(2026, 10, 16))
        async with factory() as session:
            session.add(KlineHistory(code="600002", market_type="stock", trade_date=date(2026, 10, 15), open=99, high=99, low=99, close=99, volume=1))
            await session.commit()
        app = FastAPI()
        app.include_router(router)
        app.state.stock_eod_enabled = True
        async def dependency():
            async with factory() as session:
                yield session
        app.dependency_overrides[get_session] = dependency
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="qa-reference", market_type="stock")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            raw = (await client.get("/api/stock/kline?code=600001")).json()
            assert raw["bars"][0]["pre_close"] == 5 and raw["bars"][0]["volume"] == 0.01
            assert raw["change_basis"] == "vendor_ex_rights_pre_close"
            assert raw["reference_price"] == "vendor_ex_rights_pre_close"
            assert raw["coverage"] == {
                "scope": "selected_source_local_store", "available_bars": 1,
                "first_date": "2026-10-16", "last_date": "2026-10-16",
                "requested_limit": 250, "effective_limit": 250,
                "window_status": "local_history_exhausted", "history_completeness": "unverified",
            }
            adjusted = (await client.get("/api/stock/kline?code=600002")).json()
            assert adjusted["change_basis"] == "adjacent_loaded_adjusted_close"
            assert len(adjusted["bars"]) == 1 and adjusted["bars"][0]["close"] == 99
            assert "pre_close" not in adjusted["bars"][0]
            assert adjusted["coverage"]["available_bars"] == 1
            assert adjusted["coverage"]["first_date"] == "2026-10-15"
            assert (await client.get("/api/stock/kline?code=６００００１")).status_code == 422


@pytest.mark.asyncio
async def test_stock_kline_coverage_counts_selected_store_not_return_window_or_other_source():
    from app.api.stock import _stock_kline_data
    async with database() as factory:
        async with factory() as session:
            KlineHistory.__table__.create(session.get_bind())
        for day in (14, 15, 16):
            await run_eod(factory, Source((catalog(), [row(trade_date=f"202610{day}")], False)), date(2026, 10, day))
        async with factory() as session:
            # A valid local adjusted series takes precedence and never joins raw EOD.
            for day in (12, 13):
                session.add(KlineHistory(code="600002", market_type="stock", trade_date=date(2026, 10, day), open=10, high=10, low=10, close=10, volume=1))
            await session.commit()
            raw = await _stock_kline_data(session, "600001", 1, prefer_eod=True)
            assert len(raw["bars"]) == 1 and raw["has_more"] is True
            assert raw["coverage"]["available_bars"] == 3
            assert raw["coverage"]["first_date"] == "2026-10-14"
            assert raw["coverage"]["last_date"] == "2026-10-16"
            assert raw["coverage"]["window_status"] == "window_limited"
            assert raw["coverage"]["requested_limit"] == raw["coverage"]["effective_limit"] == 1
            adjusted = await _stock_kline_data(session, "600002", 1, prefer_eod=True)
            assert adjusted["source"] == "local_import" and adjusted["coverage"]["available_bars"] == 2
            assert adjusted["coverage"]["first_date"] == "2026-10-12"
            empty = await _stock_kline_data(session, "600001", 3000, prefer_eod=False)
            assert empty["coverage"]["available_bars"] == 0
            assert empty["coverage"]["first_date"] is empty["coverage"]["last_date"] is None
            assert empty["coverage"]["window_status"] == "no_local_history"
            capped = await _stock_kline_data(session, "600001", 3000, prefer_eod=True)
            assert capped["coverage"]["effective_limit"] == 250
            assert capped["coverage"]["requested_limit"] == 3000


@pytest.mark.asyncio
async def test_eod_coverage_respects_same_cutoff_status_unknown_code_and_security_filters():
    from app.services.stock_eod import eod_coverage
    async with database() as factory:
        for day in (14, 15, 16, 17):
            await run_eod(factory, Source((catalog(), [row(trade_date=f"202610{day}")], False)), date(2026, 10, day))
        async with factory() as session:
            batches = (await session.execute(select(StockEodBatch))).scalars().all()
            for batch in batches:
                if batch.trade_date.day == 14:
                    batch.snapshot_json = {**batch.snapshot_json, "unknown_codes": ["600001"]}
                if batch.trade_date.day == 15:
                    batch.status = "failed"
            await session.commit()
            assert await eod_coverage(session, "600001", date(2026, 10, 16)) == (1, "2026-10-16", "2026-10-16")
            assert await eod_coverage(session, "600002", date(2026, 10, 16)) == (0, None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("reference", [0, -1, True, None, "5"])
async def test_stock_kline_invalid_reference_fails_closed_and_sanitized(reference):
    async with database() as factory:
        async with factory() as session:
            KlineHistory.__table__.create(session.get_bind())
        await run_eod(factory, Source((catalog(), [row()], False)), date(2026, 10, 16))
        async with factory() as session:
            source = await session.scalar(select(StockEodBatch))
            source.snapshot_json = {**source.snapshot_json, "quotes": [{**source.snapshot_json["quotes"][0], "pre_close": reference}]}
            await session.commit()
        app = FastAPI()
        app.include_router(router)
        app.state.stock_eod_enabled = True
        async def dependency():
            async with factory() as session:
                yield session
        app.dependency_overrides[get_session] = dependency
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="qa-reference", market_type="stock")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/stock/kline?code=600001")
            assert response.status_code == 503 and "bars" not in response.json()
            assert response.headers.get("X-Request-ID") and response.headers["Cache-Control"] == "no-store"
