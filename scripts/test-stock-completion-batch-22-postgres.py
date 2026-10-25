import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime
import importlib.util
from pathlib import Path
import re

from sqlalchemy import insert, text


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("completion_pg_harness", ROOT / "scripts/test-stock-completion-batch-02-postgres.py")
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)

from app.api.stock_market import router
from app.models.stock import StockFinancial, StockInstrument, StockValuation


async def additional_checks(*, check, scoped, application, client, headers, owner_id, other_id, stock_id, settings):
    async with scoped() as session:
        namespace = await session.scalar(text("SELECT current_schema()"))
        if not re.fullmatch(r"qa_completion_b02_[a-f0-9]{32}", namespace):
            raise RuntimeError("Private schema required")
        protected = ("stock_instruments", "stock_financial", "stock_valuation", "paper_accounts", "paper_positions", "paper_orders", "paper_ledgers", "ai_trading_tasks")
        before = {table: dict((await session.execute(text(f"SELECT count(*) AS rows, md5(string_agg(row_to_json(item)::text, '' ORDER BY row_to_json(item)::text)) AS digest FROM public.{table} AS item"))).mappings().one()) for table in protected}
        await session.execute(text(f'CREATE TABLE "{namespace}".stock_instruments (LIKE public.stock_instruments INCLUDING ALL)'))
        await session.execute(insert(StockInstrument), [{"id": 96000, "code": "600101", "name": "隔离历史证券", "exchange": "SSE", "industry": "金融", "updated_at": datetime(2024, 1, 1)}])
        financials = [("2023-09-30", "2024-01-03", -12), ("2023-06-30", None, None), ("2023-03-31", "2023-03-01", 99), ("2022-12-31", "2023-04-30", 1), ("2023-12-31", "2024-04-01", 99)]
        await session.execute(insert(StockFinancial), [{"id": 96000 + index, "code": "600101", "report_date": date.fromisoformat(report), "publish_date": date.fromisoformat(publish) if publish else None, "roe": roe, "updated_at": datetime(2024, 1, 1)} for index, (report, publish, roe) in enumerate(financials)])
        await session.execute(insert(StockValuation), [{"id": 96000 + index, "code": "600101", "trade_date": date(2024, 1, 2 + index), "pe_ttm": -12, "pb": None, "total_mv": 100} for index in range(2)])
        await session.commit()
    @asynccontextmanager
    async def fresh():
        async with scoped() as session:
            await session.commit()
            yield session
    application.state.session_factory = fresh
    application.include_router(router)
    parameters = {"code": "600101", "kind": "financial", "as_of": "2024-01-03", "limit": 1}
    path = "/api/stock/market/fundamentals"
    response = await client.get(path, params=parameters, headers=headers(stock_id))
    result = response.json()
    check("b22_real_PG_HTTP_readonly_and_trace", response.status_code == 200 and result["transaction"] == "postgresql_repeatable_read_readonly" and response.headers.get("Cache-Control") == "no-store" and bool(response.headers.get("X-Request-ID")))
    check("b22_disclosure_partitions_and_same_day_display", result["counts"] == {"source_rows": 5, "eligible_rows": 2, "unknown_publication": 1, "invalid_dates": 1, "later_rows": 1} and result["rows"][0]["publish_date"] == "2024-01-03")
    check("b22_negative_ROE_and_missing_margin_not_imputed", result["rows"][0]["roe"] == -12 and result["rows"][0]["gross_margin"] is None)
    second = (await client.get(path, params={**parameters, "offset": 1}, headers=headers(stock_id))).json()
    check("b22_paginated_union_exact_and_bounded", result["has_more"] is True and second["has_more"] is False and [result["rows"][0]["report_date"], second["rows"][0]["report_date"]] == ["2023-09-30", "2022-12-31"])
    check("b22_identity_and_legacy_scope_not_PIT_claim", result["instrument"]["instrument_id"] == "600101.SSE" and result["point_in_time_certified"] is False and result["data_version"] is None and result["provenance"] == "legacy_unverified")
    valuation = (await client.get(path, params={**parameters, "kind": "valuation", "as_of": "2024-01-02"}, headers=headers(stock_id, "live"))).json()
    check("b22_valuation_asof_keeps_negative_and_null", valuation["counts"]["eligible_rows"] == valuation["counts"]["later_rows"] == 1 and valuation["rows"][0]["pe_ttm"] == -12 and valuation["rows"][0]["pb"] is None)
    for token_headers, status in ((None, 401), (headers(owner_id), 409)):
        denied = await client.get(path, params=parameters, headers=token_headers)
        check(f"b22_auth_stock_gate_{status}_trace_no_store", denied.status_code == status and denied.headers.get("Cache-Control") == "no-store" and bool(denied.headers.get("X-Request-ID")))
    for label, patch, status in (("extra", {"extra": "unsafe"}, 422), ("unicode", {"code": "６００１０１"}, 422), ("future", {"as_of": "2099-01-01"}, 422), ("unknown", {"code": "600999"}, 404)):
        invalid = await client.get(path, params={**parameters, **patch}, headers=headers(stock_id))
        check(f"b22_{label}_{status}_not_fake_empty", invalid.status_code == status and "rows" not in invalid.json() and invalid.headers.get("Cache-Control") == "no-store")
    async with scoped() as session:
        await session.execute(text("UPDATE stock_valuation SET pe_ttm='Infinity'::double precision WHERE code='600101'"))
        await session.commit()
    dirty = await client.get(path, params={**parameters, "kind": "valuation"}, headers=headers(stock_id))
    check("b22_nonfinite_PG_page_refused_not_null", dirty.status_code == 409 and dirty.headers.get("X-Fundamental-Error") == "invalid_fundamental_values" and "rows" not in dirty.json())
    @asynccontextmanager
    async def broken():
        raise RuntimeError("private-sensitive-fixture")
        yield
    application.state.session_factory = broken
    failure = await client.get(path, params=parameters, headers=headers(stock_id))
    check("b22_DB_failure_503_sanitized_and_not_empty", failure.status_code == 503 and "private-sensitive" not in failure.text and failure.headers.get("Cache-Control") == "no-store")
    async with scoped() as session:
        after = {table: dict((await session.execute(text(f"SELECT count(*) AS rows, md5(string_agg(row_to_json(item)::text, '' ORDER BY row_to_json(item)::text)) AS digest FROM public.{table} AS item"))).mappings().one()) for table in protected}
        check("b22_public_financial_catalog_ledger_tasks_unchanged", before == after)


if __name__ == "__main__":
    asyncio.run(harness.main(additional_checks=additional_checks, report_label="b22"))
