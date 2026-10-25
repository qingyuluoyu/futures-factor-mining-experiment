import asyncio
from datetime import date, datetime
import hashlib
import importlib.util
from pathlib import Path
import re
from uuid import uuid4

from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


ROOT = Path(__file__).resolve().parents[1]
specification = importlib.util.spec_from_file_location("completion_pg_harness", ROOT / "scripts/test-stock-completion-batch-02-postgres.py")
harness = importlib.util.module_from_spec(specification)
specification.loader.exec_module(harness)

from app.api import stock_market, stock_replay, stock_research_jobs
from app.core.deps import get_settings
from app.models.kline_history import KlineHistory
from app.models.stock import StockInstrument
from app.models.stock_replay import StockReplayDataset
from app.models.stock_research_job import StockResearchSchemaVersion


async def additional_checks(*, check, scoped, application, client, headers, owner_id, other_id, stock_id, settings):
    async with scoped() as session:
        namespace = await session.scalar(text("SELECT current_schema()"))
        if not re.fullmatch(r"qa_completion_b02_[a-f0-9]{32}", namespace):
            raise RuntimeError("Private schema required")
        database_url = session.get_bind().url
        tables = ("kline_history", "stock_instruments", "stock_replay_datasets", "stock_replay_schema_versions")
        public_counts = [await session.scalar(text(f"SELECT count(*) FROM public.{table}")) for table in tables]
        for table in tables:
            await session.execute(text(f'CREATE TABLE "{namespace}".{table} (LIKE public.{table} INCLUDING ALL)'))
        await session.execute(insert(StockInstrument), [{"id": 91000 + index, "code": f"60010{index}", "name": f"B16 synthetic {index}", "exchange": "SSE", "industry": "fixture", "updated_at": datetime(2024, 1, 1)} for index in range(1, 5)])
        rows = [{"id": 91000 + index, "market_type": market, "code": code, "trade_date": stamp, "open": price, "high": price, "low": price, "close": price, "volume": 100} for index, (market, code, stamp, price) in enumerate([
            ("stock", "600101", date(2024, 1, 2), 10), ("stock", "600101", date(2024, 1, 3), 11),
            ("stock", "600102", date(2024, 1, 3), 20), ("futures", "600103", date(2024, 1, 3), 999),
            ("stock", "600104", date(2099, 1, 1), 999),
        ])]
        await session.execute(insert(KlineHistory), rows)
        session.add(StockResearchSchemaVersion(version=stock_research_jobs.SCHEMA_VERSION, checksum=hashlib.sha256(stock_research_jobs.SCHEMA_PATH.read_bytes()).hexdigest()))
        await session.execute(text("INSERT INTO stock_replay_schema_versions (version, checksum) VALUES ('20261017_001_stock_replay', :checksum)"), {"checksum": hashlib.sha256((ROOT / "backend/migrations/20261017_001_stock_replay.sql").read_bytes()).hexdigest()})
        await session.execute(insert(StockReplayDataset), [{"id": uuid4(), "version": f"b16-fixture-{index}", "code": "600101", "instrument_id": "600101.SH", "name": "B16 synthetic replay", "start_date": date(2024, 1, 2), "end_date": date(2024, 1, 3), "bars_json": [], "meta": {"supplier": "synthetic_private_fixture_not_validated_for_trading"}, "created_at": datetime(2024, 1, 1)} for index in range(101)])
        await session.commit()
    engine = create_async_engine(database_url, echo=False, hide_parameters=True, connect_args={"timeout": 5, "command_timeout": 10, "server_settings": {"search_path": f'"{namespace}",public', "statement_timeout": "10000"}})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    previous_factory = application.state.session_factory
    application.state.session_factory = factory
    application.include_router(stock_market.router)
    application.include_router(stock_replay.router)
    application.include_router(stock_research_jobs.router)
    application.dependency_overrides[get_settings] = lambda: settings.model_copy(update={"stock_research_jobs_enabled": True, "stock_replay_enabled": True})
    try:
        paths = ["/api/stock/market/boards?limit=200&offset=0", "/api/stock/research/coverage?start_date=2024-01-02&end_date=2024-01-03", "/api/stock/replay/availability", "/api/stock/research/availability"]
        for path in paths:
            check("b16_source_anonymous_401", (await client.get(path)).status_code == 401)
            check("b16_source_futures_market_409", (await client.get(path, headers=headers(owner_id))).status_code == 409)
        results = [await client.get(path, headers=headers(stock_id)) for path in paths]
        check("b16_four_real_SQL_sources_200", all(response.status_code == 200 for response in results))
        market, coverage, replay, research = [response.json() for response in results]
        check("b16_catalog_quote_history_are_distinct", market["coverage"] == {"instruments": 4, "quoted": 2, "comparable": 1} and coverage["available"]["securities"] == 2 and coverage["available"]["price_rows"] == 3)
        check("b16_future_and_futures_do_not_extend_history", coverage["available"]["last_trade_date"] == market["as_of"] == "2024-01-03")
        check("b16_missing_cell_preserved_and_not_certified", coverage["selected"]["missing_price_cells"] == 1 and coverage["status"] == "within_limits" and any("不保证" in warning for warning in coverage["warnings"]))
        check("b16_registered_dataset_listing_is_capped_not_total", replay["enabled"] is True and len(replay["datasets"]) == 100 and all(row["adjustment"] == "none" and row["version"].startswith("b16-fixture-") for row in replay["datasets"]))
        check("b16_research_data_does_not_imply_worker_accepting", research["enabled"] is True and research["accepting"] is False and research["reason"] is not None)
        for suffix in ("start_date=2024-02-30", "start_date=2024-01-03&end_date=2024-01-02", "end_date=2099-01-01"):
            check("b16_invalid_period_422", (await client.get("/api/stock/research/coverage?" + suffix, headers=headers(stock_id))).status_code == 422)
        application.dependency_overrides[get_settings] = lambda: settings.model_copy(update={"stock_replay_enabled": False, "stock_research_jobs_enabled": False})
        disabled = (await client.get("/api/stock/replay/availability", headers=headers(stock_id))).json()
        check("b16_disabled_replay_not_registered_zero_claim", disabled == {"enabled": False, "datasets": []})
        async with scoped() as session:
            check("b16_public_source_counts_unchanged", [await session.scalar(text(f"SELECT count(*) FROM public.{table}")) for table in tables] == public_counts)
    finally:
        application.state.session_factory = previous_factory
        application.dependency_overrides[get_settings] = lambda: settings
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(harness.main(additional_checks=additional_checks, report_label="b16"))
