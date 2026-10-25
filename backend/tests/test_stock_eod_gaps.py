from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session

from app.api.stock_market import router
from app.core.deps import get_current_user, get_session
from app.models.kline_history import KlineHistory
from app.models.stock import StockInstrument
from app.models.stock_eod import StockEodBatch
from app.services.stock_eod import METHOD_VERSION, content_hash, normalize_eod


def sample_snapshot():
    catalog = [{"code": code, "name": code, "exchange": "SSE", "industry": "fixture", "list_date": date(2000, 1, 1)} for code in ("600101", "600102", "600103")]
    quotes = [{"ts_code": code + ".SH", "trade_date": "20240103", "open": 10, "high": 10, "low": 10, "close": 10, "pre_close": 10, "vol": 123.45, "amount": 100} for code in ("600101", "600104")]
    return normalize_eod(quotes, catalog, date(2024, 1, 3), acquired_at=datetime(2024, 1, 3, 9, tzinfo=timezone.utc), has_more=False, source_evidence={"version": "fixture", "private_token": "never-echo-fixture"})


@pytest.fixture
def gap_app():
    database = create_engine("sqlite:///:memory:")
    for model in (StockInstrument, KlineHistory, StockEodBatch):
        model.__table__.create(database)
    snapshot = sample_snapshot()
    now = datetime(2024, 1, 3, 9, tzinfo=timezone.utc)
    with Session(database) as session:
        session.add_all([StockInstrument(code=code, name=name, exchange="SSE", industry="fixture", updated_at=now) for code, name in (("600101", "有价"), ("600102", "缺口股票"), ("600104", "后来入目录"))])
        for index, (market, stamp) in enumerate((("stock", date(2024, 1, 2)), ("stock", date(2024, 1, 4)), ("futures", date(2024, 1, 1)))):
            session.add(KlineHistory(id=index + 1, market_type=market, code="600102", trade_date=stamp, open=10, high=10, low=10, close=10, volume=100))
        for stamp, status in ((date(2024, 1, 3), "partially_completed"), (date(2099, 1, 1), "completed"), (date(2026, 10, 20), "failed")):
            session.add(StockEodBatch(trade_date=stamp, status=status, step=status, request_id="fixture", attempts=1, started_at=now, deadline_at=now + timedelta(seconds=60), finished_at=now, method_version=METHOD_VERSION, data_version=snapshot["data_version"], snapshot_json=deepcopy(snapshot)))
        session.commit()

    class Adapter:
        def __init__(self, session):
            self.session = session

        async def execute(self, statement):
            return self.session.execute(statement)

        async def scalar(self, statement):
            return self.session.scalar(statement)

    async def dependency():
        with Session(database) as session:
            yield Adapter(session)

    application = FastAPI()
    application.include_router(router)
    application.state.stock_eod_enabled = True
    application.dependency_overrides[get_session] = dependency
    application.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="qa-gaps", market_type="stock")
    yield application, database
    database.dispose()


@pytest.mark.asyncio
async def test_gap_SQL_pages_preserve_snapshot_membership_and_history_cutoff(gap_app):
    application, database = gap_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        first = await client.get("/api/stock/market/eod-gaps?limit=1")
        assert first.status_code == 200
        payload = first.json()
        assert payload["gap_version"] == "stock-eod-gaps-v1" and payload["state"] == "ready"
        assert payload["totals"] == {"missing": 2, "unknown": 1}
        assert payload["snapshot"]["trade_date"] == "2024-01-03"
        assert payload["items"][0]["code"] == "600102"
        assert payload["items"][0]["history"] == {"rows": 1, "first_date": "2024-01-02", "last_date": "2024-01-02"}
        assert payload["has_more"] and first.headers.get("X-Request-ID") and first.headers.get("Cache-Control") == "no-store"
        collected = [payload["items"][0]["code"]]
        for offset in (1, 2):
            following = await client.get(f"/api/stock/market/eod-gaps?limit=1&offset={offset}&expected_fingerprint={payload['query_fingerprint']}")
            assert following.status_code == 200
            collected.extend(row["code"] for row in following.json()["items"])
        assert collected == ["600102", "600103", "600104"]
        unknown = (await client.get("/api/stock/market/eod-gaps?kind=unknown")).json()["items"][0]
        assert unknown["kind"] == "unknown" and unknown["catalog_status"] == "present_now" and unknown["name"] == "后来入目录"
        assert unknown["instrument_id_basis"] == "source_snapshot"
        missing = (await client.get("/api/stock/market/eod-gaps?keyword=缺口股票")).json()
        assert missing["total"] == 1 and missing["items"][0]["code"] == "600102"
        assert "never-echo-fixture" not in first.text and "private_token" not in first.text


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["kind=tradable", "limit=101", "offset=20001", "expected_fingerprint=bad", "keyword=" + "a" * 65, "unexpected=1"])
async def test_gap_invalid_queries_reject_with_trace(gap_app, query):
    application, _ = gap_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        response = await client.get("/api/stock/market/eod-gaps?" + query)
    assert response.status_code == 422 and response.headers.get("X-Request-ID")
    assert response.headers.get("Cache-Control") == "no-store"


@pytest.mark.asyncio
async def test_gap_catalog_change_rejects_mixed_pages(gap_app):
    application, database = gap_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        first = (await client.get("/api/stock/market/eod-gaps?limit=1")).json()
        with Session(database) as session:
            session.execute(update(StockInstrument).where(StockInstrument.code == "600102").values(name="renamed"))
            session.commit()
        response = await client.get(f"/api/stock/market/eod-gaps?limit=1&offset=1&expected_fingerprint={first['query_fingerprint']}")
    assert response.status_code == 409 and response.headers.get("X-Stock-Gap-Error") == "snapshot_changed"


@pytest.mark.asyncio
async def test_gap_disabled_and_no_snapshot_are_not_zero_gap_certification(gap_app):
    application, database = gap_app
    application.state.stock_eod_enabled = False
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        disabled = (await client.get("/api/stock/market/eod-gaps")).json()
        assert disabled["state"] == "disabled" and disabled["totals"] is None and disabled["snapshot"] is None
        application.state.stock_eod_enabled = True
        with Session(database) as session:
            session.query(StockEodBatch).delete()
            session.commit()
        absent = (await client.get("/api/stock/market/eod-gaps")).json()
        assert absent["state"] == "no_snapshot" and absent["totals"] is None


@pytest.mark.asyncio
async def test_gap_corrupt_snapshot_is_failure_without_private_details(gap_app):
    application, database = gap_app
    with Session(database) as session:
        row = session.query(StockEodBatch).filter_by(trade_date=date(2024, 1, 3)).one()
        row.snapshot_json = {**row.snapshot_json, "missing_codes": []}
        session.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        response = await client.get("/api/stock/market/eod-gaps")
    assert response.status_code == 503 and response.headers.get("Cache-Control") == "no-store"
    assert "never-echo-fixture" not in response.text


def test_gap_semantically_invalid_self_hashed_snapshot_still_fails():
    from app.services.stock_eod_gaps import validate_gap_snapshot

    snapshot = sample_snapshot()
    snapshot["missing_codes"] = ["600102", "600102"]
    snapshot["data_version"] = content_hash({key: value for key, value in snapshot.items() if key not in ("data_version", "collected_at")})
    with pytest.raises(ValueError):
        validate_gap_snapshot(snapshot, date(2024, 1, 3), snapshot["data_version"])


@pytest.mark.parametrize("change", ["numeric_pagination_flag", "string_volume", "negative_amount", "unicode_missing"])
def test_gap_hash_does_not_replace_normalized_semantic_validation(change):
    from app.services.stock_eod_gaps import validate_gap_snapshot

    snapshot = sample_snapshot()
    if change == "numeric_pagination_flag":
        snapshot["vendor_has_more"] = 0
    elif change == "string_volume":
        snapshot["quotes"][0]["volume"] = "123.45"
    elif change == "negative_amount":
        snapshot["quotes"][0]["amount"] = -1
    else:
        snapshot["missing_codes"][0] = "６００１０２"
    snapshot["data_version"] = content_hash({key: value for key, value in snapshot.items() if key not in ("data_version", "collected_at")})
    with pytest.raises(ValueError):
        validate_gap_snapshot(snapshot, date(2024, 1, 3), snapshot["data_version"])
