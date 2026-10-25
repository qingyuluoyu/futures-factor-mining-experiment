import asyncio
from datetime import datetime, timedelta
import importlib.util
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from sqlalchemy import insert, text, update


ROOT = Path(__file__).resolve().parents[1]
specification = importlib.util.spec_from_file_location("completion_pg_harness", ROOT / "scripts/test-stock-completion-batch-02-postgres.py")
harness = importlib.util.module_from_spec(specification)
specification.loader.exec_module(harness)

from app.api.stock_market import router
from app.models.kline_history import KlineHistory
from app.models.stock import StockInstrument


async def additional_checks(*, check, scoped, application, client, headers, owner_id, other_id, stock_id, settings):
    application.include_router(router)
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    async with scoped() as session:
        namespace = await session.scalar(text("SELECT current_schema()"))
        if not re.fullmatch(r"qa_completion_b02_[a-f0-9]{32}", namespace):
            raise RuntimeError("Private schema required")
        public_counts = [await session.scalar(text(f"SELECT count(*) FROM public.{table}")) for table in ("kline_history", "stock_instruments")]
        for table in ("kline_history", "stock_instruments"):
            await session.execute(text(f'CREATE TABLE "{namespace}".{table} (LIKE public.{table} INCLUDING ALL)'))
        await session.execute(insert(StockInstrument), [{"id": 90000 + index, "code": f"60010{index}", "name": f"B15 synthetic {index}", "exchange": "SSE", "industry": "fixture", "updated_at": datetime.now()} for index in range(1, 5)])
        rows = []
        for index, previous, current, volume in ((1, 10, 10, 0), (2, 20, 22, 500)):
            for stamp, price in ((today - timedelta(days=1), previous), (today, current), (today + timedelta(days=1), 999)):
                rows.append({"id": 90000 + len(rows), "market_type": "stock", "code": f"60010{index}", "trade_date": stamp, "open": price, "high": price, "low": price, "close": price, "volume": volume})
        rows.extend([
            {"id": 90100, "market_type": "stock", "code": "600103", "trade_date": today, "open": 5, "high": 5, "low": 5, "close": 5, "volume": 100},
            {"id": 90101, "market_type": "futures", "code": "600104", "trade_date": today, "open": 999, "high": 999, "low": 999, "close": 999, "volume": 100},
        ])
        await session.execute(insert(KlineHistory), rows)
        await session.commit()
    endpoint = "/api/stock/market/screen"
    check("b15_anonymous_401", (await client.get(endpoint)).status_code == 401)
    check("b15_futures_market_rejected_409_without_switching_user", (await client.get(endpoint, headers=headers(owner_id))).status_code == 409)
    query = "min_close=9&max_close=22&min_change_pct=0&max_change_pct=10&min_volume=0&sort=change_pct&direction=desc&limit=1"
    codes = []
    fingerprint = None
    for offset in (0, 1, 2):
        anchor = f"&expected_fingerprint={fingerprint}" if fingerprint else ""
        response = await client.get(f"{endpoint}?{query}&offset={offset}{anchor}", headers=headers(stock_id))
        payload = response.json()
        fingerprint = payload["screen_fingerprint"]
        check("b15_real_PG_AND_filter_contract_with_trace", response.status_code == 200 and payload["total"] == 2 and payload["screen_version"] == "stock-screen-v1" and response.headers.get("X-Request-ID") and response.headers.get("Cache-Control") == "no-store")
        check("b15_page_conditions_echo_and_bounds", payload["applied_filters"]["offset"] == offset and len(payload["items"]) == (1 if offset < 2 else 0) and payload["has_more"] == (offset == 0))
        codes.extend(row["code"] for row in payload["items"])
        if offset == 1:
            check("b15_zero_change_volume_not_filled_or_dropped", payload["items"][0]["change_pct"] == 0 and payload["items"][0]["volume"] == 0)
    check("b15_paginated_union_exact_no_duplicates", codes == ["600102", "600101"])
    catalog = (await client.get(endpoint, headers=headers(stock_id))).json()
    check("b15_catalog_preserves_null_and_does_not_use_futures_or_future_bars", catalog["total"] == 4 and next(row for row in catalog["items"] if row["code"] == "600104")["close"] is None and next(row for row in catalog["items"] if row["code"] == "600102")["close"] == 22)
    check("b15_volume_units_are_explicit", catalog["filter_units"]["volume"] == catalog["volume_unit"] == "lot_100_shares")
    old = (await client.get("/api/stock/market/members", headers=headers(stock_id))).json()
    check("b15_legacy_members_payload_unchanged", old["items"] == catalog["items"] and "screen_version" not in old)
    async with scoped() as session:
        await session.execute(update(KlineHistory).where(KlineHistory.code == "600101", KlineHistory.market_type == "stock", KlineHistory.trade_date == today).values(open=11, high=11, low=11, close=11))
        await session.commit()
    changed = await client.get(f"{endpoint}?{query}&offset=1&expected_fingerprint={fingerprint}", headers=headers(stock_id))
    check("b15_real_PG_changed_snapshot_rejected_409_without_mixing_pages", changed.status_code == 409 and changed.headers.get("X-Stock-Screen-Error") == "snapshot_changed" and changed.headers.get("Cache-Control") == "no-store")
    for suffix in ("min_close=22&max_close=10", "max_close=nan", "min_change_pct=10&max_change_pct=0", "min_volume=-1", "limit=101", "offset=10001", "unknown=1"):
        response = await client.get(endpoint + "?" + suffix, headers=headers(stock_id))
        check("b15_invalid_conditions_422_with_trace", response.status_code == 422 and bool(response.headers.get("X-Request-ID")))
    async with scoped() as session:
        check("b15_public_quote_catalog_tables_unchanged", [await session.scalar(text(f"SELECT count(*) FROM public.{table}")) for table in ("kline_history", "stock_instruments")] == public_counts)


if __name__ == "__main__":
    asyncio.run(harness.main(additional_checks=additional_checks, report_label="b15"))
