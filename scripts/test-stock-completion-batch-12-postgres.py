import asyncio
from datetime import date, datetime
import hashlib
import importlib.util
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

import pandas as pd
from sqlalchemy import insert, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


ROOT = Path(__file__).resolve().parents[1]
specification = importlib.util.spec_from_file_location("completion_pg_harness", ROOT / "scripts/test-stock-completion-batch-02-postgres.py")
harness = importlib.util.module_from_spec(specification)
specification.loader.exec_module(harness)

from app.api import stock_research_jobs as api
from app.core.deps import get_settings
from app.models.kline_history import KlineHistory
from app.models.stock import StockFinancial, StockInstrument, StockValuation
from app.models.stock_research_job import StockResearchSchemaVersion
from app.services.stock_factors.coverage import get_research_coverage
from app.services.stock_factors.engine import BacktestParams, ResearchDataError, run_portfolio_backtest
from app.services.stock_factors.snapshots import load_versioned_market_data
from app.services.stock_research_jobs import claim_next_job, finish_job, release_worker_slot
from app.services import stock_research_process


async def additional_checks(*, check, scoped, application, client, headers, owner_id, other_id, stock_id, settings):
    async with scoped() as session:
        namespace = await session.scalar(text("SELECT current_schema()"))
        if not re.fullmatch(r"qa_completion_b02_[a-f0-9]{32}", namespace):
            raise RuntimeError("Private schema required")
        database_url = session.get_bind().url
        public_prices = await session.scalar(text("SELECT count(*) FROM public.kline_history"))
        for table in ("kline_history", "stock_instruments"):
            await session.execute(text(f'CREATE TABLE "{namespace}".{table} (LIKE public.{table} INCLUDING ALL)'))
        dates = [value.date() for value in pd.bdate_range("2024-01-02", periods=80)]
        prices, valuations, instruments, financials = [], [], [], []
        for index in range(30):
            code = f"{600101 + index:06d}"
            instruments.append({"id": 1000 + index, "code": code, "name": f"B12-synthetic-{index}", "exchange": "SSE", "industry": "fixture-A" if index < 15 else "fixture-B", "list_date": date(2020, 1, 1), "updated_at": datetime(2024, 1, 1)})
            financials.append({"id": 1000 + index, "code": code, "report_date": date(2023, 12, 31), "publish_date": dates[0], "roe": 2.0 + index, "gross_margin": 10.0, "revenue_yoy": 5.0, "netprofit_yoy": 6.0})
            for day, stamp in enumerate(dates):
                value = 100 + index + day * (index + 1) / 100
                prices.append({"id": 1000 + index * 80 + day, "market_type": "stock", "code": code, "trade_date": stamp, "open": value, "high": value + 1, "low": value - 1, "close": value, "volume": 100, "hold": 0})
                valuations.append({"id": 1000 + index * 80 + day, "code": code, "trade_date": stamp, "pe_ttm": 10.0 + index, "pb": 1.0 + index / 10, "turnover_rate": 2.0, "total_mv": 100.0 + index})
        for model, rows in ((StockInstrument, instruments), (KlineHistory, prices), (StockValuation, valuations), (StockFinancial, financials)):
            await session.execute(insert(model), rows)
        session.add(StockResearchSchemaVersion(version=api.SCHEMA_VERSION, checksum=hashlib.sha256(api.SCHEMA_PATH.read_bytes()).hexdigest()))
        await session.commit()
    engine = create_async_engine(database_url, echo=False, hide_parameters=True, connect_args={"timeout": 5, "command_timeout": 10,
        "server_settings": {"search_path": f'"{namespace}",public', "statement_timeout": "10000"}})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    previous_factory = application.state.session_factory
    application.state.session_factory = factory
    application.state.stock_research_worker = SimpleNamespace(healthy=True)
    application.include_router(api.router)
    application.dependency_overrides[get_settings] = lambda: settings.model_copy(update={"stock_research_jobs_enabled": True})
    try:
        path = "/api/stock/research/coverage-v2?factor_ids=ret_20d&start_date=2024-01-02&end_date=" + dates[-1].isoformat()
        check("b12_real_JWT_anonymous_401", (await client.get(path)).status_code == 401)
        check("b12_real_JWT_futures_market_409", (await client.get(path, headers=headers(owner_id))).status_code == 409)
        selected_response = await client.get(path, headers=headers(stock_id))
        check("b12_real_JWT_v2_price_only_four_auxiliary_groups_omitted", selected_response.status_code == 200 and selected_response.json()["selected"]["panel_cells"] == 2400 and selected_response.json()["selected"]["valuation_rows"] == selected_response.json()["selected"]["financial_rows"] == 0)
        old_response = await client.get(path.replace("coverage-v2?factor_ids=ret_20d&", "coverage?"), headers=headers(stock_id))
        check("b12_v1_legacy_count_unchanged", old_response.status_code == 200 and old_response.json()["selected"]["panel_cells"] == 21600 and "dependencies" not in old_response.json())
        for suffix in ("factor_ids=unknown", "factor_ids=roe&factor_ids=roe", "factor_ids=roe&weight=unknown"):
            check("b12_invalid_dependencies_422", (await client.get("/api/stock/research/coverage-v2?" + suffix, headers=headers(stock_id))).status_code == 422)
        cap = (await client.get("/api/stock/research/availability", headers=headers(stock_id))).json()
        check("b12_selective_coverage_capability_is_explicit", cap["selective_coverage"] is True)
        full, full_manifest = await load_versioned_market_data(factory, dates[0], dates[-1])
        selected, manifest = await load_versioned_market_data(factory, dates[0], dates[-1], factor_ids=["ret_20d"])
        pd.testing.assert_frame_equal(full.close, selected.close)
        check("b12_postgres_read_only_repeatable_read_verified", manifest["transaction"]["read_only"] and manifest["transaction"]["isolation_level"] == "repeatable read")
        params = BacktestParams(factor_ids=["ret_20d"], start_date=dates[0].isoformat(), end_date=dates[-1].isoformat(), top_n=5)
        result = run_portfolio_backtest(selected, params)
        check("b12_real_PG_full_and_selective_backtests_exactly_equal", not result.get("error") and result == run_portfolio_backtest(full, params))
        emitted = []
        def private_connection_engine(url, **options):
            if url.host not in ("127.0.0.1", "localhost") or url.port != 5433 or url.database != database_url.database:
                raise RuntimeError("Only private local process calculation connection permitted")
            options.setdefault("connect_args", {})["server_settings"] = {"search_path": f'"{namespace}",public'}
            return create_async_engine(url, **options)
        process_parameters = {"factor_ids": ["ret_20d"], "start_date": dates[0].isoformat(), "end_date": dates[-1].isoformat(), "top_n": 5, "weight": "mktcap"}
        with patch("sqlalchemy.ext.asyncio.create_async_engine", side_effect=private_connection_engine), patch.object(stock_research_process, "emit", side_effect=emitted.append):
            calculated = await stock_research_process.calculate("backtest", process_parameters)
        check("b12_process_calculate_uses_real_PG_and_forwards_cap_weight_dependencies", emitted[0]["manifest"]["dependencies"]["valuation_fields"] == ["total_mv"] and emitted[0]["manifest"]["dependencies"]["financial_fields"] == [])
        check("b12_process_calculate_and_full_core_results_equal", calculated == run_portfolio_backtest(full, BacktestParams(**process_parameters)))
        for factor, options in (("roe", {}), ("ret_20d", {"weight": "mktcap"}), ("roe", {"mktcap_neutral": True})):
            data, snapshot = await load_versioned_market_data(factory, dates[0], dates[-1], factor_ids=[factor], **options)
            coverage = await get_research_coverage(factory, dates[0], dates[-1], factor_ids=[factor], **options)
            check("b12_preflight_and_actual_cells_and_fields_match", coverage["selected"]["panel_cells"] == snapshot["coverage"]["panel_cells"] and coverage["dependencies"] == snapshot["dependencies"])
            if factor == "roe":
                check("b12_financial_available_strictly_after_publication", pd.isna(data.financial["roe"].iloc[0, 0]) and data.financial["roe"].iloc[1, 0] == 2)
            if options:
                pd.testing.assert_frame_equal(full.valuation["total_mv"], data.valuation["total_mv"])
                check("b12_market_cap_not_silently_omitted", "total_mv" in data.valuation)
        command = {"kind": "backtest", "idempotency_key": str(uuid4()), "parameters": {"factor_ids": ["ret_20d"], "start_date": dates[0].isoformat(), "end_date": dates[-1].isoformat(), "top_n": 5}}
        created = await client.post("/api/stock/research/jobs", json=command, headers=headers(stock_id))
        check("b12_real_HTTP_task_created_before_manual_core_execution", created.status_code == 202)
        identifier = UUID(created.json()["id"])
        async with scoped() as session:
            claimed = await claim_next_job(session)
            token = claimed.worker_token
            check("b12_real_PG_claim_is_scoped_task", claimed.id == identifier and claimed.trading_mode == "virtual")
            await session.commit()
        async with scoped() as session:
            check("b12_real_calculation_and_selective_manifest_persisted", await finish_job(session, identifier, token, result=result, manifest=manifest))
            await release_worker_slot(session, identifier, token)
            await session.commit()
        saved = await client.get(f"/api/stock/research/jobs/{identifier}/result", headers=headers(stock_id))
        check("b12_real_HTTP_result_exposes_selective_provenance", saved.status_code == 200 and saved.json()["data_manifest"]["dependencies"] == manifest["dependencies"])
        for identity, mode in ((other_id, "virtual"), (stock_id, "live")):
            denied = await client.get(f"/api/stock/research/jobs/{identifier}/result", headers=headers(identity, mode))
            check("b12_saved_result_rejects_foreign_owner_or_JWT_mode", denied.status_code in (404, 409))
        async with scoped() as session:
            await session.execute(update(StockValuation).where(StockValuation.code == "600101").values(pb=float("inf")))
            await session.commit()
        _, unchanged = await load_versioned_market_data(factory, dates[0], dates[-1], factor_ids=["ret_20d"])
        check("b12_unrequested_bad_auxiliary_data_not_read_or_hashed", unchanged["data_version"] == manifest["data_version"])
        try:
            await load_versioned_market_data(factory, dates[0], dates[-1], factor_ids=["pb"])
            raise AssertionError("Bad requested PB must fail")
        except ResearchDataError as error:
            check("b12_requested_bad_data_still_fails", error.code == "invalid_market_data")
        async with scoped() as session:
            check("b12_no_public_price_writes", await session.scalar(text("SELECT count(*) FROM public.kline_history")) == public_prices)
    finally:
        application.state.session_factory = previous_factory
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(harness.main(additional_checks=additional_checks, report_label="b12"))
