import asyncio
from datetime import date, datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import re

from sqlalchemy import insert, select, text


ROOT = Path(__file__).resolve().parents[1]
specification = importlib.util.spec_from_file_location("completion_pg_harness", ROOT / "scripts/test-stock-completion-batch-02-postgres.py")
harness = importlib.util.module_from_spec(specification)
specification.loader.exec_module(harness)

from app.models.kline_history import KlineHistory
from app.models.stock import StockInstrument
from app.models.stock_eod import StockEodBatch
from app.services.stock_eod import METHOD_VERSION, eod_bars, normalize_eod


async def additional_checks(*, check, scoped, application, client, headers, owner_id, other_id, stock_id, settings):
    now = datetime.now(timezone.utc)
    async with scoped() as session:
        namespace = await session.scalar(text("SELECT current_schema()"))
        if not re.fullmatch(r"qa_completion_b02_[a-f0-9]{32}", namespace):
            raise RuntimeError("Private schema required")
        public_tables = ("stock_instruments", "stock_eod_batches", "paper_accounts", "paper_positions", "paper_orders", "paper_ledgers", "ai_trading_tasks")
        before = {table: dict((await session.execute(text(f"SELECT count(*) AS rows, md5(string_agg(row_to_json(item)::text, '' ORDER BY row_to_json(item)::text)) AS digest FROM public.{table} AS item"))).mappings().one()) for table in public_tables}
        legacy_before = dict((await session.execute(text("SELECT count(*) AS rows,max(id) AS last_id,max(trade_date) AS last_date FROM public.kline_history"))).mappings().one())
        for table in ("stock_instruments", "stock_eod_batches", "kline_history"):
            await session.execute(text(f'CREATE TABLE "{namespace}".{table} (LIKE public.{table} INCLUDING ALL)'))
        instruments = [{"id": 94000 + index, "code": code, "name": "B20-fixture", "industry": "fixture", "exchange": "SSE", "list_date": date(2000, 1, 1), "updated_at": now.replace(tzinfo=None)} for index, code in enumerate(("600101", "600102"))]
        await session.execute(insert(StockInstrument), instruments)
        quote = {"ts_code": "600101.SH", "trade_date": "20261020", "open": 10, "high": 12, "low": 9, "close": 11, "pre_close": 5, "vol": 0.01, "amount": 0.001}
        snapshot = normalize_eod([quote, {**quote, "ts_code": "600102.SH"}], instruments, date(2026, 10, 20), acquired_at=now, has_more=False)
        source = StockEodBatch(trade_date=date(2026, 10, 20), status="completed", step="completed", request_id="b20-private", attempts=1,
            started_at=now, deadline_at=now + timedelta(seconds=60), finished_at=now, method_version=METHOD_VERSION, data_version=snapshot["data_version"], snapshot_json=snapshot)
        session.add(source)
        await session.execute(insert(KlineHistory), [
            {"id": 94000, "market_type": "stock", "code": "600102", "trade_date": date(2026, 10, 16), "open": 99, "high": 99, "low": 99, "close": 99, "volume": 100},
            {"id": 94001, "market_type": "stock", "code": "600102", "trade_date": date(2026, 10, 22), "open": 333, "high": 333, "low": 333, "close": 333, "volume": 100},
        ])
        await session.commit()
    application.state.stock_eod_enabled = True
    path = "/api/stock/kline?code=600101"
    raw_response = await client.get(path, headers=headers(stock_id))
    raw = raw_response.json()
    check("b20_real_PG_raw_vendor_reference_and_fractional_lot", raw_response.status_code == 200 and len(raw["bars"]) == 1 and raw["bars"][0]["pre_close"] == 5 and raw["bars"][0]["volume"] == 0.01)
    check("b20_reference_contract_and_trace", raw["change_basis"] == raw["reference_price"] == "vendor_ex_rights_pre_close" and raw["source"] == "teajoin_eod" and raw_response.headers.get("X-Request-ID") and raw_response.headers.get("Cache-Control") == "no-store")
    adjusted = (await client.get("/api/stock/kline?code=600102", headers=headers(stock_id))).json()
    check("b20_adjusted_precedence_without_raw_append_or_future_bar", adjusted["change_basis"] == "adjacent_loaded_adjusted_close" and len(adjusted["bars"]) == 1 and adjusted["bars"][0]["close"] == 99 and "pre_close" not in adjusted["bars"][0])
    check("b20_unchanged_JWT_and_futures_boundary", (await client.get(path)).status_code == 401 and (await client.get(path, headers=headers(owner_id))).status_code == 409)
    check("b20_ASCII_only_and_bounded_limit", (await client.get("/api/stock/kline?code=６００１０１", headers=headers(stock_id))).status_code == 422 and (await client.get(path + "&limit=3001", headers=headers(stock_id))).status_code == 422)
    async with scoped() as session:
        legacy = await eod_bars(session, "600101", date(2026, 10, 20), 250)
        check("b20_legacy_eod_bar_helper_shape_unchanged", "pre_close" not in legacy[0] and legacy[0]["volume"] == 0.01)
    application.state.stock_eod_enabled = False
    disabled = (await client.get(path, headers=headers(stock_id))).json()
    check("b20_disabled_EOD_does_not_fabricate_bars", disabled["bars"] == [] and disabled["price_adjustment"] == "qfq")
    application.state.stock_eod_enabled = True
    async with scoped() as session:
        saved = await session.scalar(select(StockEodBatch).where(StockEodBatch.trade_date == date(2026, 10, 20)))
        saved.snapshot_json = {**snapshot, "quotes": [{**item, "pre_close": "private-fixture-payload"} for item in snapshot["quotes"]]}
        await session.commit()
    invalid = await client.get(path, headers=headers(stock_id))
    check("b20_corrupt_reference_fails_sanitized_not_empty", invalid.status_code == 503 and "private-fixture-payload" not in invalid.text and "bars" not in invalid.json() and invalid.headers.get("X-Request-ID"))
    async with scoped() as session:
        after = {table: dict((await session.execute(text(f"SELECT count(*) AS rows, md5(string_agg(row_to_json(item)::text, '' ORDER BY row_to_json(item)::text)) AS digest FROM public.{table} AS item"))).mappings().one()) for table in public_tables}
        check("b20_public_business_full_row_summaries_unchanged", before == after)
        check("b20_public_legacy_history_counts_unchanged", legacy_before == dict((await session.execute(text("SELECT count(*) AS rows,max(id) AS last_id,max(trade_date) AS last_date FROM public.kline_history"))).mappings().one()))


if __name__ == "__main__":
    asyncio.run(harness.main(additional_checks=additional_checks, report_label="b20"))
