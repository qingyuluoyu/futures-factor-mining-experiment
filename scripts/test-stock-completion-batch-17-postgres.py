import asyncio
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import re

from sqlalchemy import insert, text, update


ROOT = Path(__file__).resolve().parents[1]
specification = importlib.util.spec_from_file_location("completion_pg_harness", ROOT / "scripts/test-stock-completion-batch-02-postgres.py")
harness = importlib.util.module_from_spec(specification)
specification.loader.exec_module(harness)

from app.api import stock_market
from app.models.kline_history import KlineHistory
from app.models.stock import StockInstrument
from app.models.stock_eod import StockEodBatch
from app.services.stock_eod import METHOD_VERSION, normalize_eod


async def additional_checks(*, check, scoped, application, client, headers, owner_id, other_id, stock_id, settings):
    now = datetime(2024, 1, 3, 9, tzinfo=timezone.utc)
    original_catalog = [{"code": code, "name": "fixture-original", "exchange": "SSE", "industry": "fixture", "list_date": date(2000, 1, 1)} for code in ("600101", "600102", "600103")]
    quotes = [{"ts_code": code + ".SH", "trade_date": "20240103", "open": 10, "high": 10, "low": 10, "close": 10, "pre_close": 10, "vol": 123.45, "amount": 100} for code in ("600101", "600104")]
    snapshot = normalize_eod(quotes, original_catalog, date(2024, 1, 3), acquired_at=now, has_more=False, source_evidence={"version": "private_fixture", "secret_fixture": "not-to-be-returned"})
    async with scoped() as session:
        namespace = await session.scalar(text("SELECT current_schema()"))
        if not re.fullmatch(r"qa_completion_b02_[a-f0-9]{32}", namespace):
            raise RuntimeError("Private schema required")
        tables = ("stock_instruments", "kline_history", "stock_eod_batches")
        public_counts = [await session.scalar(text(f"SELECT count(*) FROM public.{table}")) for table in tables]
        for table in tables:
            await session.execute(text(f'CREATE TABLE "{namespace}".{table} (LIKE public.{table} INCLUDING ALL)'))
        await session.execute(insert(StockInstrument), [{"id": 92000 + index, "code": code, "name": name, "exchange": "SSE", "industry": "fixture", "updated_at": now.replace(tzinfo=None)} for index, (code, name) in enumerate((("600101", "B17-fixture-quoted"), ("600102", "B17-fixture-missing"), ("600104", "B17-fixture-new-catalog")))])
        await session.execute(insert(KlineHistory), [{"id": 92000 + index, "market_type": market, "code": "600102", "trade_date": stamp, "open": 10, "high": 10, "low": 10, "close": 10, "volume": 100} for index, (market, stamp) in enumerate((("stock", date(2024, 1, 2)), ("stock", date(2024, 1, 4)), ("futures", date(2024, 1, 1))))])
        for stamp, status in ((date(2024, 1, 3), "partially_completed"), (date(2099, 1, 1), "completed"), (date(2026, 10, 20), "failed")):
            session.add(StockEodBatch(trade_date=stamp, status=status, step=status, request_id="b17-private-fixture", attempts=1, started_at=now, deadline_at=now + timedelta(seconds=60), finished_at=now, method_version=METHOD_VERSION, data_version=snapshot["data_version"], snapshot_json=deepcopy(snapshot)))
        await session.commit()
    application.include_router(stock_market.router)
    application.state.stock_eod_enabled = True
    path = "/api/stock/market/eod-gaps"
    check("b17_anonymous_401", (await client.get(path)).status_code == 401)
    check("b17_futures_session_409_no_preferences_changed", (await client.get(path, headers=headers(owner_id))).status_code == 409)
    first_response = await client.get(path + "?limit=1", headers=headers(stock_id))
    check("b17_true_SQL_endpoint_trace_no_store", first_response.status_code == 200 and first_response.headers.get("X-Request-ID") and first_response.headers.get("Cache-Control") == "no-store")
    first = first_response.json()
    check("b17_latest_success_excludes_failed_and_future_batches", first["snapshot"]["trade_date"] == "2024-01-03" and first["totals"] == {"missing": 2, "unknown": 1})
    check("b17_history_cutoff_excludes_future_and_futures", first["items"][0]["history"] == {"rows": 1, "first_date": "2024-01-02", "last_date": "2024-01-02"})
    codes = [first["items"][0]["code"]]
    for offset in (1, 2):
        following = await client.get(f"{path}?limit=1&offset={offset}&expected_fingerprint={first['query_fingerprint']}", headers=headers(stock_id))
        check("b17_fingerprint_page_200", following.status_code == 200)
        codes.extend(row["code"] for row in following.json()["items"])
    check("b17_page_union_exact", codes == ["600102", "600103", "600104"])
    unknown = (await client.get(path + "?kind=unknown", headers=headers(stock_id))).json()["items"][0]
    check("b17_new_catalog_does_not_promote_snapshot_unknown", unknown["kind"] == "unknown" and unknown["catalog_status"] == "present_now" and unknown["instrument_id_basis"] == "source_snapshot")
    check("b17_evidence_is_allowlisted_not_secret_or_certified", "not-to-be-returned" not in first_response.text and "secret_fixture" not in first_response.text and first["snapshot"]["verification"] == "stored_metadata_only_not_independent_certification")
    async with scoped() as session:
        await session.execute(update(StockInstrument).where(StockInstrument.code == "600102").values(name="B17-fixture-renamed"))
        await session.commit()
    changed = await client.get(f"{path}?limit=1&offset=1&expected_fingerprint={first['query_fingerprint']}", headers=headers(stock_id))
    check("b17_catalog_change_stops_mixed_pages_409", changed.status_code == 409 and changed.headers.get("X-Stock-Gap-Error") == "snapshot_changed")
    for query in ("kind=tradable", "offset=20001", "limit=101", "unexpected=1"):
        response = await client.get(path + "?" + query, headers=headers(stock_id))
        check("b17_invalid_query_422_no_store_trace", response.status_code == 422 and response.headers.get("Cache-Control") == "no-store" and bool(response.headers.get("X-Request-ID")))
    async with scoped() as session:
        await session.execute(update(StockEodBatch).where(StockEodBatch.trade_date == date(2024, 1, 3)).values(snapshot_json={**snapshot, "missing_codes": []}))
        await session.commit()
    corrupt = await client.get(path, headers=headers(stock_id))
    check("b17_corrupt_hash_is_503_not_empty_success", corrupt.status_code == 503 and "not-to-be-returned" not in corrupt.text)
    application.state.stock_eod_enabled = False
    disabled = (await client.get(path, headers=headers(stock_id))).json()
    check("b17_disabled_is_not_zero_gap_certificate", disabled["state"] == "disabled" and disabled["totals"] is None)
    async with scoped() as session:
        check("b17_public_source_tables_unchanged", [await session.scalar(text(f"SELECT count(*) FROM public.{table}")) for table in tables] == public_counts)


if __name__ == "__main__":
    asyncio.run(harness.main(additional_checks=additional_checks, report_label="b17"))
