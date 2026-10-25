import asyncio
from contextlib import asynccontextmanager
import hashlib
import importlib.util
from pathlib import Path
import re
from types import SimpleNamespace
from uuid import UUID, uuid4

import pandas as pd
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


ROOT = Path(__file__).resolve().parents[1]
specification = importlib.util.spec_from_file_location("completion_pg_harness", ROOT / "scripts/test-stock-completion-batch-02-postgres.py")
harness = importlib.util.module_from_spec(specification)
specification.loader.exec_module(harness)

from app.api import stock_research_jobs as job_api
from app.api.stock_factors import router as factors_router
from app.core.deps import get_session, get_settings
from app.models.stock_research_job import StockResearchJob, StockResearchSchemaVersion
from app.models.user import User
from app.services.stock_factors.engine import BacktestParams, MarketData, run_portfolio_backtest
from app.services.stock_research_jobs import claim_next_job, finish_job, release_worker_slot


async def additional_checks(*, check, scoped, application, client, headers, owner_id, other_id, stock_id, settings):
    application.include_router(factors_router)
    application.include_router(job_api.router)
    configured = settings.model_copy(update={"stock_research_jobs_enabled": True, "stock_legacy_sync_enabled": False})
    application.dependency_overrides[get_settings] = lambda: configured
    application.state.stock_research_worker = SimpleNamespace(healthy=True)
    async with scoped() as session:
        await session.execute(update(User).where(User.id == other_id).values(market_type="stock"))
        session.add(StockResearchSchemaVersion(version=job_api.SCHEMA_VERSION, checksum=hashlib.sha256(job_api.SCHEMA_PATH.read_bytes()).hexdigest()))
        await session.commit()
    parameters = {"factor_id": "ret_20d", "start_date": "20240101", "end_date": "20240430"}
    key = str(uuid4())
    agreed = {**headers(stock_id), "Prefer": "respond-async", "Idempotency-Key": key}
    path = "/api/stock/factors/analysis"
    available = await client.get("/api/stock/research/availability", headers=headers(stock_id))
    check("b09_capability_reflects_registered_adapter_and_retired_sync", available.status_code == 200 and available.json()["legacy_async_adapter"] and available.json()["legacy_sync_enabled"] is False)
    check("b09_async_requires_jwt", (await client.post(path, json=parameters, headers={"Prefer": "respond-async", "Idempotency-Key": key})).status_code == 401)
    check("b09_futures_cannot_enqueue_stock", (await client.post(path, json=parameters, headers={**headers(owner_id), "Prefer": "respond-async", "Idempotency-Key": key})).status_code == 409)
    disabled = await client.post(path, json=parameters, headers=headers(stock_id))
    check("b09_retired_sync_rejected_before_reading_public_prices", disabled.status_code == 503 and disabled.headers.get("X-Research-Error") == "legacy_sync_disabled" and bool(disabled.headers.get("X-Request-ID")))
    command = {"kind": "analysis", "idempotency_key": key, "parameters": parameters}
    concurrent = await asyncio.gather(client.post(path, json=parameters, headers=agreed), client.post("/api/stock/research/jobs", json=command, headers=headers(stock_id)))
    check("b09_cross_entry_concurrent_requests_share_exact_one_job", all(item.status_code == 202 for item in concurrent) and concurrent[0].json()["id"] == concurrent[1].json()["id"])
    created = concurrent[0]
    job_id = UUID(created.json()["id"])
    check("b09_negotiated_response_metadata_and_no_inline_execution", created.headers.get("Preference-Applied") == "respond-async" and created.headers.get("Location") == f"/api/stock/research/jobs/{job_id}" and created.headers.get("Cache-Control") == "no-store" and created.json()["attempts"] == 0)
    async with scoped() as session:
        check("b09_single_durable_row_after_concurrent_submissions", await session.scalar(select(func.count()).select_from(StockResearchJob)) == 1)
    occupied = await client.post(path, json=parameters, headers={**agreed, "Idempotency-Key": str(uuid4())})
    check("b09_shared_user_queue_backpressure", occupied.status_code == 429 and occupied.headers.get("Retry-After") == "3")
    application.state.stock_research_worker.healthy = False
    replay = await client.post(path, json=parameters, headers=agreed)
    check("b09_worker_offline_same_key_reconciliation", replay.status_code == 202 and replay.json()["id"] == str(job_id))
    conflict = await client.post(path, json={**parameters, "groups": 3}, headers=agreed)
    check("b09_worker_offline_changed_intent_still_409", conflict.status_code == 409)
    check("b09_worker_offline_new_key_not_inserted", (await client.post(path, json=parameters, headers={**agreed, "Idempotency-Key": str(uuid4())})).status_code == 503)
    for identity, mode in ((other_id, "virtual"), (stock_id, "live")):
        check("b09_report_owner_and_jwt_mode_404", (await client.get(f"/api/stock/research/jobs/{job_id}", headers=headers(identity, mode))).status_code == 404)
        check("b09_same_key_in_other_scope_does_not_reconcile_foreign_task", (await client.post(path, json=parameters, headers={**headers(identity, mode), "Prefer": "respond-async", "Idempotency-Key": key})).status_code == 503)
    cancelled = await client.post(f"/api/stock/research/jobs/{job_id}/cancel", headers=headers(stock_id))
    check("b09_cancel_works_even_when_executor_offline", cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled")
    check("b09_cancelled_same_key_not_restarted", (await client.post(path, json=parameters, headers=agreed)).json()["status"] == "cancelled")
    async with scoped() as session:
        namespace = await session.scalar(text("SELECT current_schema()"))
        database_url = session.get_bind().url
    if not re.fullmatch(r"qa_completion_b02_[a-f0-9]{32}", namespace):
        raise RuntimeError("Invalid isolated schema")
    limited_engine = create_async_engine(database_url, pool_size=1, max_overflow=0, pool_timeout=1, echo=False)
    limited_factory = async_sessionmaker(limited_engine, expire_on_commit=False)

    @asynccontextmanager
    async def limited_scoped():
        async with limited_factory() as session:
            await session.execute(text(f'SET search_path TO "{namespace}", public'))
            await session.execute(text("SET LOCAL statement_timeout = '3s'"))
            yield session

    async def limited_dependency():
        async with limited_scoped() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    original_dependency = application.dependency_overrides[get_session]
    application.dependency_overrides[get_session] = limited_dependency
    application.state.session_factory = limited_scoped
    try:
        responses = await asyncio.wait_for(asyncio.gather(*(client.post(path, json=parameters, headers=agreed) for _ in range(2))), timeout=4)
        check("b09_single_connection_real_pool_has_no_auth_submission_self_starvation", all(item.status_code == 202 and item.json()["id"] == str(job_id) for item in responses))
    finally:
        application.dependency_overrides[get_session] = original_dependency
        application.state.session_factory = scoped
        await limited_engine.dispose()
    application.state.stock_research_worker.healthy = True
    backtest_parameters = {"factor_ids": ["ret_20d"], "start_date": "20240101", "end_date": "20240430", "top_n": 3}
    backtest = await client.post("/api/stock/backtest", json=backtest_parameters, headers={**agreed, "Idempotency-Key": str(uuid4())})
    check("b09_backtest_adapter_uses_same_queue", backtest.status_code == 202 and backtest.json()["kind"] == "backtest")
    dates = pd.bdate_range("2024-01-01", "2024-04-30")
    close = pd.DataFrame({str(600001 + index): [100 + index + day * (index + 1) / 100 for day in range(len(dates))] for index in range(30)}, index=dates)
    result = run_portfolio_backtest(MarketData(close=close, valuation={}, financial={}, industry={}, name={}), BacktestParams(**backtest_parameters))
    check("b09_real_engine_deterministic_fixture_not_vendor_certification", not result.get("error") and bool(result["nav"]))
    async with scoped() as session:
        claimed = await claim_next_job(session)
        check("b09_worker_state_machine_claims_only_pending_backtest", claimed is not None and str(claimed.id) == backtest.json()["id"] and claimed.attempts == 1)
        token = claimed.worker_token
        manifest = {"data_version": "b" * 64, "immutable_archive": False, "source": "synthetic_private_fixture_not_market_certification"}
        check("b09_finished_result_committed_through_real_service", await finish_job(session, claimed.id, token, result=result, manifest=manifest))
        check("b09_worker_compute_slot_released", await release_worker_slot(session, claimed.id, token))
        await session.commit()
    reopened = await client.get(f"/api/stock/research/jobs/{backtest.json()['id']}/result", headers=headers(stock_id))
    check("b09_backtest_result_reopens_exact_saved_calculation", reopened.status_code == 200 and all(reopened.json().get(name) == value for name, value in result.items()))
    async with scoped() as session:
        check("b09_only_two_rows_no_hidden_sync_or_foreign_jobs", await session.scalar(select(func.count()).select_from(StockResearchJob)) == 2)


if __name__ == "__main__":
    asyncio.run(harness.main(additional_checks=additional_checks, report_label="b09"))
