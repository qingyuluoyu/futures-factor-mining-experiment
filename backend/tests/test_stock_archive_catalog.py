from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.services.stock_daily_archive import FINANCIAL_METADATA, METHOD_VERSION
from app.services.stock_eod import content_hash


def batch():
    source = "a" * 64
    return dict(id=uuid4(), version=content_hash({"method_version": METHOD_VERSION, "source_data_version": source}),
        source_data_version=source, method_version=METHOD_VERSION, trade_date=date(2026, 10, 21),
        status="completed", source_collected_at=datetime(2026, 10, 21, 9, tzinfo=timezone.utc),
        archived_at=datetime(2026, 10, 21, 10, tzinfo=timezone.utc), row_count=5548, expected_count=5558,
        missing_count=10, unknown_count=20, source_metadata={**FINANCIAL_METADATA, "catalog_version": "b" * 64,
            "source_row_count": 5568, "verification": "snapshot_only_not_independently_certified", "data_available_at": None,
            "number_encoding": "decimal_of_stored_json_number_not_original_response_text", "source_evidence": {"private": "must_not_return"}})


def test_catalog_metadata_is_validated_and_never_certifies_research_or_exposes_evidence():
    from app.services.stock_archive_catalog import catalog_item
    result = catalog_item(batch(), snapshot_at=datetime(2026, 10, 22, tzinfo=timezone.utc))
    assert result["row_count"] == 5548 and result["missing_count"] == 10
    assert result["research_ready"] is result["replay_ready"] is False
    assert result["verification"] == "metadata_only_not_row_or_PIT_certification"
    assert "source_evidence" not in result and "source_metadata" not in result
    assert result["price_adjustment"] == "none" and result["volume_unit"] == "lot_100_shares"


@pytest.mark.parametrize("patch", [
    {"version": "c" * 64}, {"expected_count": 5548}, {"row_count": True}, {"status": "running"},
    {"source_collected_at": datetime(2026, 10, 21, 9)},
    {"archived_at": datetime(2026, 10, 20, 10, tzinfo=timezone.utc)},
    {"source_collected_at": datetime(2026, 10, 21, 8, tzinfo=timezone.utc)},
])
def test_corrupt_or_preclose_metadata_is_not_a_successful_empty_catalog(patch):
    from app.services.stock_archive_catalog import catalog_item
    with pytest.raises(ValueError):
        catalog_item({**batch(), **patch}, snapshot_at=datetime(2026, 10, 22, tzinfo=timezone.utc))


@pytest.mark.parametrize("field,value", [("price_adjustment", "qfq"), ("source_row_count", 5548), ("catalog_version", "bad"), ("data_available_at", "2026-10-21"), ("provider", "unapproved")])
def test_financial_source_and_version_metadata_cannot_be_silently_relabelled(field, value):
    from app.services.stock_archive_catalog import catalog_item
    item = batch(); item["source_metadata"][field] = value
    with pytest.raises(ValueError):
        catalog_item(item, snapshot_at=datetime(2026, 10, 22, tzinfo=timezone.utc))


def test_missing_explicit_unknown_available_time_is_not_invented():
    from app.services.stock_archive_catalog import catalog_item
    item = batch(); del item["source_metadata"]["data_available_at"]
    with pytest.raises(ValueError):
        catalog_item(item, snapshot_at=datetime(2026, 10, 22, tzinfo=timezone.utc))


@pytest.mark.asyncio
async def test_catalog_route_keeps_market_auth_bounded_query_and_failure_headers(monkeypatch):
    from app.api import stock_archive_catalog as module
    from app.core.deps import get_current_user, get_session
    app = FastAPI(); app.include_router(module.router, prefix="/api/stock/market")
    app.state.stock_archive_ready = True
    user = SimpleNamespace(id=uuid4(), market_type="stock")
    app.dependency_overrides[get_current_user] = lambda: user
    async def session():
        yield object()
    app.dependency_overrides[get_session] = session
    calls = []
    async def read(session, query, *, now):
        calls.append(query)
        raise ValueError("private upstream details")
    monkeypatch.setattr(module, "read_archive_catalog", read)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for params in ({"limit": 21}, {"offset": 10001}, {"offset": 1}, {"trade_date": "2026-02-30"}, {"trade_date": "20261021"}, {"snapshot_at": "2026-10-21T00:00:00"}, {"unexpected": "x"}, {"snapshot_at": "9999-01-01T00:00:00Z", "membership_token": "a" * 32}, {"snapshot_at": "2026-10-19T00:00:00Z", "membership_token": "a" * 32, "trade_date": "2026-10-21"}):
            result = await client.get("/api/stock/market/archive/versions", params=params)
            assert result.status_code == 422
            assert result.headers["Cache-Control"] == "no-store" and result.headers["X-Request-ID"]
        assert not calls
        app.state.stock_archive_ready = False
        assert (await client.get("/api/stock/market/archive/versions")).status_code == 503 and not calls
        app.state.stock_archive_ready = True
        result = await client.get("/api/stock/market/archive/versions")
        assert result.status_code == 503 and "private upstream" not in result.text
        assert result.headers["X-Request-ID"] and result.headers["Cache-Control"] == "no-store"
        user.market_type = "futures"
        assert (await client.get("/api/stock/market/archive/versions")).status_code == 409
        assert len(calls) == 1
