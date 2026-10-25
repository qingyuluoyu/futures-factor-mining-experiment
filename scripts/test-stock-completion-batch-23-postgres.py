import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import re
from uuid import UUID, uuid4

from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("completion_pg_harness", ROOT / "scripts/test-stock-completion-batch-02-postgres.py")
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)

from app.api.stock_replay import router
from app.core.deps import get_session, get_settings
from app.models.stock_replay import StockReplayDataset, StockReplayOrder


async def additional_checks(*, check, scoped, application, client, headers, owner_id, other_id, stock_id, settings):
    async with scoped() as session:
        namespace = await session.scalar(text("SELECT current_schema()"))
        if not re.fullmatch(r"qa_completion_b02_[a-f0-9]{32}", namespace):
            raise RuntimeError("Private schema required")
        protected = ("stock_replay_datasets", "stock_replay_accounts", "stock_replay_orders", "stock_replay_steps", "paper_accounts", "paper_positions", "paper_orders", "paper_ledgers", "ai_trading_tasks")
        before = {table: dict((await session.execute(text(f"SELECT count(*) AS rows, md5(string_agg(row_to_json(item)::text, '' ORDER BY row_to_json(item)::text)) AS digest FROM public.{table} AS item"))).mappings().one()) for table in protected}
        for table in ("stock_replay_datasets", "stock_replay_accounts", "stock_replay_orders", "stock_replay_steps", "stock_replay_schema_versions"):
            await session.execute(text(f'CREATE TABLE "{namespace}".{table} (LIKE public.{table} INCLUDING ALL)'))
        await session.execute(text("INSERT INTO stock_replay_schema_versions SELECT * FROM public.stock_replay_schema_versions WHERE version='20261017_001_stock_replay'"))
        await session.execute(text("UPDATE users SET market_type='stock' WHERE id=:id"), {"id": other_id})
        dataset_id = uuid4()
        bars = [{"trade_date": f"2026-09-0{day}", "open": "10", "high": "11", "low": "9", "close": "11", "volume_shares": 100000, "adj_factor": "1", "up_limit": "12", "down_limit": "8"} for day in (1, 2, 3)]
        await session.execute(insert(StockReplayDataset), [{"id": dataset_id, "version": "c" * 64, "code": "000001", "instrument_id": "000001.SZ", "name": "私有撮合夹具", "start_date": date(2026, 9, 1), "end_date": date(2026, 9, 3), "bars_json": bars, "meta": {"supplier": "synthetic_private_fixture", "calendar_dates": [row["trade_date"] for row in bars]}}])
        await session.commit()
    @asynccontextmanager
    async def fresh():
        async with scoped() as session:
            await session.commit()
            yield session
    application.state.session_factory = fresh
    application.dependency_overrides[get_settings] = lambda: settings.model_copy(update={"stock_replay_enabled": True})
    application.include_router(router)
    stock_headers = headers(stock_id)
    created = await client.post("/api/stock/replay/accounts", headers=stock_headers, json={"idempotency_key": str(uuid4()), "name": "私有一万元", "initial_cash": "10000", "start_date": "2026-09-01", "dataset_ids": [str(dataset_id)], "slippage_rate": "0"})
    check("b23_original_HTTP_account_created_in_private_schema", created.status_code == 200 and created.json()["cash"] == "10000.00")
    account_id = created.json()["id"]
    base = f"/api/stock/replay/accounts/{account_id}"
    ordered = await client.post(base + "/orders", headers=stock_headers, json={"idempotency_key": str(uuid4()), "expected_revision": 0, "code": "000001", "direction": "buy", "quantity": 100})
    check("b23_original_HTTP_pending_order", ordered.status_code == 200 and ordered.json()["revision"] == 1)
    advanced = await client.post(base + "/step", headers=stock_headers, json={"idempotency_key": str(uuid4()), "expected_revision": 1})
    check("b23_real_PG_matching_cash_fees_equity", advanced.status_code == 200 and advanced.json()["cash"] == "8994.99" and advanced.json()["equity"] == "10094.99" and advanced.json()["fees_paid"] == "5.01")
    limited = await client.post(base + "/orders", headers=stock_headers, json={"idempotency_key": str(uuid4()), "expected_revision": 2, "code": "000001", "direction": "buy", "quantity": 100, "order_type": "limit", "limit_price": "9"})
    pending_id = next(item["id"] for item in limited.json()["orders"] if item["status"] == "pending")
    cancelled = await client.post(base + f"/orders/{pending_id}/cancel", headers=stock_headers, json={"idempotency_key": str(uuid4()), "expected_revision": 3})
    check("b23_original_cancel_cash_and_reservation_preserved", cancelled.status_code == 200 and cancelled.json()["revision"] == 4 and cancelled.json()["cash"] == "8994.99" and cancelled.json()["reserved_cash"] == "0.00")
    async with scoped() as session:
        await session.execute(insert(StockReplayOrder), [{"id": uuid4(), "account_id": UUID(account_id), "idempotency_key": uuid4(), "params_hash": "b" * 64, "code": "000001", "direction": "buy", "quantity": 100, "order_type": "market", "status": "rejected", "reason": "liquidity_limit", "submitted_on": date(2026, 9, 1), "created_at": datetime(2026, 9, 1, tzinfo=timezone.utc) + timedelta(seconds=index)} for index in range(210)])
        await session.execute(text("UPDATE stock_replay_accounts SET revision=214 WHERE id=:id"), {"id": UUID(account_id)})
        await session.commit()
        private_before = {table: dict((await session.execute(text(f"SELECT count(*) AS rows, md5(string_agg(row_to_json(item)::text, '' ORDER BY row_to_json(item)::text)) AS digest FROM {table} AS item"))).mappings().one()) for table in protected[:4]}
    path = base + "/order-history"
    params = {"expected_revision": 214, "limit": 100}
    pages = []
    for offset in (0, 100, 200):
        response = await client.get(path, headers=stock_headers, params={**params, "offset": offset})
        check(f"b23_page_{offset}_readonly_trace_no_store", response.status_code == 200 and response.json()["transaction"] == "postgresql_repeatable_read_readonly" and response.headers.get("Cache-Control") == "no-store" and bool(response.headers.get("X-Request-ID")))
        pages.append(response.json())
    check("b23_all_212_history_accessible_beyond_old_200", [len(page["items"]) for page in pages] == [100, 100, 12] and len({item["id"] for page in pages for item in page["items"]}) == 212 and all(page["total"] == 212 for page in pages))
    old = (await client.get(base, headers=stock_headers)).json()
    check("b23_old_view_keeps_200_window_and_financial_values", len(old["orders"]) == 200 and old["cash"] == "8994.99" and old["equity"] == "10094.99")
    filled = await client.get(path, headers=stock_headers, params={"expected_revision": 214, "status": "filled", "direction": "buy", "code": "000001"})
    fill_result = filled.json()
    check("b23_actual_matched_order_fields_preserved", filled.status_code == 200 and fill_result["total"] == 1 and fill_result["items"][0]["fees"] == "5.01" and fill_result["items"][0]["cash_delta"] == "-1005.01" and fill_result["items"][0]["filled_price"] == "10.0000")
    for label, auth, status in (("anonymous", None, 401), ("futures", headers(owner_id), 409), ("other_owner", headers(other_id), 404), ("other_mode", headers(stock_id, "live"), 404)):
        denied = await client.get(path, headers=auth, params=params)
        check(f"b23_{label}_{status}_no_store_trace", denied.status_code == status and denied.headers.get("Cache-Control") == "no-store" and bool(denied.headers.get("X-Request-ID")))
    for label, patch, status in (("revision", {"expected_revision": 213}, 409), ("future", {"end": "2026-09-03"}, 422), ("unbound", {"code": "600001"}, 422), ("extra", {"extra": "unsafe"}, 422)):
        invalid_response = await client.get(path, headers=stock_headers, params={**params, **patch})
        check(f"b23_{label}_not_empty_success", invalid_response.status_code == status and "items" not in invalid_response.json() and invalid_response.headers.get("Cache-Control") == "no-store")
        if label == "revision":
            check("b23_revision_conflict_code_requires_refresh", invalid_response.headers.get("X-Replay-History-Error") == "history_revision_conflict")
    application.dependency_overrides[get_settings] = lambda: settings.model_copy(update={"stock_replay_enabled": False})
    disabled = await client.get(path, headers=stock_headers, params=params)
    check("b23_original_feature_flag_keeps_history_disabled", disabled.status_code == 503 and disabled.headers.get("Cache-Control") == "no-store")
    application.dependency_overrides[get_settings] = lambda: settings.model_copy(update={"stock_replay_enabled": True})
    async with scoped() as session:
        source_url = session.get_bind().url
    limited_engine = create_async_engine(source_url, pool_size=1, max_overflow=0, pool_timeout=0.2)
    limited_factory = async_sessionmaker(limited_engine, expire_on_commit=False)
    @asynccontextmanager
    async def limited_scoped():
        async with limited_factory() as session:
            await session.execute(text(f'SET search_path TO "{namespace}", public'))
            yield session
    async def limited_dependency():
        async with limited_scoped() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
    @asynccontextmanager
    async def limited_fresh():
        async with limited_scoped() as session:
            await session.commit()
            yield session
    previous_dependency = application.dependency_overrides[get_session]
    application.dependency_overrides[get_session] = limited_dependency
    application.state.session_factory = limited_fresh
    try:
        single = await client.get(path, headers=stock_headers, params=params)
        check("b23_history_works_with_only_one_PG_connection", single.status_code == 200 and single.json()["total"] == 212)
        concurrent = await asyncio.gather(*(client.get(path, headers=stock_headers, params={**params, "limit": 1}) for _ in range(5)))
        check("b23_five_concurrent_reads_share_one_connection_without_starvation", all(item.status_code == 200 and item.json()["revision"] == 214 for item in concurrent))
        check("b23_limited_pool_returns_all_borrowed_connections", limited_engine.pool.checkedout() == 0)
    finally:
        application.dependency_overrides[get_session] = previous_dependency
        application.state.session_factory = fresh
        await limited_engine.dispose()
    @asynccontextmanager
    async def broken():
        raise RuntimeError("sensitive-private-fixture")
        yield
    application.state.session_factory = broken
    failed = await client.get(path, headers=stock_headers, params=params)
    check("b23_database_failure_sanitized_503", failed.status_code == 503 and "sensitive-private" not in failed.text and "items" not in failed.json())
    async with scoped() as session:
        after = {table: dict((await session.execute(text(f"SELECT count(*) AS rows, md5(string_agg(row_to_json(item)::text, '' ORDER BY row_to_json(item)::text)) AS digest FROM public.{table} AS item"))).mappings().one()) for table in protected}
        private_after = {table: dict((await session.execute(text(f"SELECT count(*) AS rows, md5(string_agg(row_to_json(item)::text, '' ORDER BY row_to_json(item)::text)) AS digest FROM {table} AS item"))).mappings().one()) for table in protected[:4]}
        check("b23_history_reads_do_not_change_private_account_orders_steps_or_datasets", private_before == private_after)
        check("b23_public_replay_futures_ledgers_AI_unchanged", before == after)
    output = ROOT / "logs" / f"b23-private-response-contracts-{uuid4().hex}.json"
    with output.open("x", encoding="utf-8") as destination:
        json.dump({"scope": "synthetic_private_schema_not_public_user_or_market_certification", "results": [*pages, fill_result], "public_tables_before": before, "public_tables_after": after}, destination, ensure_ascii=False, allow_nan=False, indent=2)
    print("private_response_contracts", output.name)


if __name__ == "__main__":
    asyncio.run(harness.main(additional_checks=additional_checks, report_label="b23"))
