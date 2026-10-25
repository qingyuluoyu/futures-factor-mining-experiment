import asyncio
from contextlib import asynccontextmanager
from datetime import date
import importlib.util
from pathlib import Path
import re

from sqlalchemy import insert, text


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("completion_pg_harness", ROOT / "scripts/test-stock-completion-batch-02-postgres.py")
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)

from app.api.stock_research_jobs import router
from app.models.kline_history import KlineHistory
from app.models.stock import StockFinancial, StockValuation


async def additional_checks(*, check, scoped, application, client, headers, owner_id, other_id, stock_id, settings):
    async with scoped() as session:
        namespace = await session.scalar(text("SELECT current_schema()"))
        if not re.fullmatch(r"qa_completion_b02_[a-f0-9]{32}", namespace):
            raise RuntimeError("Private schema required")
        before = {}
        for table in ("stock_valuation", "stock_financial", "paper_accounts", "paper_positions", "paper_orders", "paper_ledgers", "ai_trading_tasks"):
            before[table] = dict((await session.execute(text(f"SELECT count(*) AS rows, md5(string_agg(row_to_json(item)::text, '' ORDER BY row_to_json(item)::text)) AS digest FROM public.{table} AS item"))).mappings().one())
        await session.execute(text(f'CREATE TABLE "{namespace}".kline_history (LIKE public.kline_history INCLUDING DEFAULTS)'))
        rows, values = [], []
        for index, (code, day) in enumerate((("600101", 2), ("600101", 3), ("600102", 2), ("600102", 3))):
            rows.append({"id": 95000 + index, "market_type": "stock", "code": code, "trade_date": date(2024, 1, day), "open": 10, "high": 10, "low": 10, "close": 10, "volume": 100})
            values.append({"id": 95000 + index, "code": code, "trade_date": date(2024, 1, day), "pe_ttm": [-2, 5, None, float("inf")][index], "pb": [-1, 0, -3, None][index], "total_mv": 0})
        rows.append({"id": 95005, "market_type": "futures", "code": "AU", "trade_date": date(2024, 1, 2), "open": 10, "high": 10, "low": 10, "close": 10, "volume": 100})
        await session.execute(insert(KlineHistory), rows)
        await session.execute(insert(StockValuation), values)
        await session.execute(insert(StockFinancial), [
            {"id": 95000, "code": "600101", "report_date": date(2023, 9, 30), "publish_date": date(2023, 11, 1), "roe": -12},
            {"id": 95001, "code": "600102", "report_date": date(2023, 9, 30), "publish_date": date(2024, 1, 3), "roe": 99},
        ])
        await session.commit()
    @asynccontextmanager
    async def fresh():
        async with scoped() as session:
            await session.commit()
            yield session
    application.state.session_factory = fresh
    application.include_router(router)
    parameters = {"start_date": "2024-01-02", "end_date": "2024-01-03", "factor_ids": ["pe_ttm", "pb", "roe", "ret_20d"], "mktcap_neutral": "true"}
    path = "/api/stock/research/inputs"
    response = await client.get(path, params=parameters, headers=headers(stock_id))
    check("b21_real_PG_HTTP_counts_and_trace", response.status_code == 200 and response.headers.get("X-Request-ID") and response.headers.get("Cache-Control") == "no-store")
    result = response.json()
    fields = {item["field"]: item for item in result["fields"]}
    check("b21_PE_domain_null_and_infinity_partitions", fields["pe_ttm"]["source_rows"] == 4 and fields["pe_ttm"]["usable_values"] == 1 and fields["pe_ttm"]["nonfinite_values"] == fields["pe_ttm"]["missing_values"] == fields["pe_ttm"]["excluded_nonpositive"] == 1)
    check("b21_positive_PB_and_market_cap_missing_not_imputed", fields["pb"]["usable_values"] == fields["total_mv"]["usable_values"] == 0 and result["auxiliary_missing"] == ["total_mv"])
    check("b21_prior_negative_ROE_valid_and_publish_day_excluded", fields["roe"]["source_rows"] == fields["roe"]["usable_values"] == 1)
    check("b21_market_and_readonly_scope_honest", result["prices"]["rows"] == 4 and result["transaction"] == "postgresql_repeatable_read_readonly" and result["status"] == "missing_inputs" and result["point_in_time_certified"] is False)
    for token_headers, expected in ((None, 401), (headers(owner_id), 409)):
        denied = await client.get(path, params=parameters, headers=token_headers)
        check(f"b21_auth_market_{expected}_no_store", denied.status_code == expected and denied.headers.get("Cache-Control") == "no-store" and bool(denied.headers.get("X-Request-ID")))
    invalid = await client.get(path, params={**parameters, "extra": "unsafe"}, headers=headers(stock_id))
    check("b21_strict_extra_query_rejected", invalid.status_code == 422 and invalid.headers.get("Cache-Control") == "no-store")
    async with scoped() as session:
        duplicate = {**rows[0], "id": 95006}
        await session.execute(insert(KlineHistory), [duplicate])
        await session.execute(text("UPDATE kline_history SET close=0 WHERE id=95001"))
        await session.commit()
    changed = (await client.get(path, params=parameters, headers=headers(stock_id))).json()
    check("b21_dirty_legacy_duplicates_and_bad_close_not_hidden", changed["status"] == "invalid_inputs" and changed["prices"]["duplicate_groups"] == changed["prices"]["invalid_rows"] == 1)
    @asynccontextmanager
    async def broken():
        raise RuntimeError("private-fixture-error")
        yield
    application.state.session_factory = broken
    failure = await client.get(path, params=parameters, headers=headers(stock_id))
    check("b21_database_failure_sanitized_not_zero", failure.status_code == 503 and "private-fixture-error" not in failure.text and "prices" not in failure.json())
    async with scoped() as session:
        after = {table: dict((await session.execute(text(f"SELECT count(*) AS rows, md5(string_agg(row_to_json(item)::text, '' ORDER BY row_to_json(item)::text)) AS digest FROM public.{table} AS item"))).mappings().one()) for table in before}
        check("b21_public_business_tables_unchanged", before == after)


if __name__ == "__main__":
    asyncio.run(harness.main(additional_checks=additional_checks, report_label="b21"))
