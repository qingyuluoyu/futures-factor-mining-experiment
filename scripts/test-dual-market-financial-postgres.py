import asyncio
from datetime import datetime
from decimal import Decimal
import hashlib
import importlib.util
import json
from pathlib import Path
import time
from uuid import UUID, uuid4

import pandas as pd
from pydantic import TypeAdapter
from redis.asyncio import Redis
from sqlalchemy import select, text, update


ROOT = Path(__file__).resolve().parents[1]
specification = importlib.util.spec_from_file_location("completion_pg_harness", ROOT / "scripts/test-stock-completion-batch-02-postgres.py")
harness = importlib.util.module_from_spec(specification)
specification.loader.exec_module(harness)

from app.api.paper import router as paper_router
from app.api.stock_factors import router as factor_router
from app.api import stock_research_jobs as job_api
from app.core.deps import get_settings
from app.models.kline_history import KlineHistory
from app.models.paper.account import PaperAccount
from app.models.stock import StockInstrument, StockValuation
from app.models.stock_research_job import StockResearchSchemaVersion
from app.models.user import User
from app.schemas.stock_research_jobs import CreateResearchJob
from app.services.market_keys import quote_key
from app.services.paper.valuation import account_valuation
from app.services.stock_factors.engine import METHOD_VERSION
from app.services.stock_research_jobs import ResearchJobService, claim_next_job, finish_job, release_worker_slot


async def additional_checks(*, check, scoped, application, client, headers, owner_id, other_id, stock_id, settings):
    if settings.redis_host not in {"localhost", "127.0.0.1"} or settings.redis_port != 6380:
        raise RuntimeError("Only dedicated local Redis6380 permitted")
    application.include_router(paper_router)
    application.include_router(factor_router)
    application.include_router(job_api.router)
    application.dependency_overrides[get_settings] = lambda: settings.model_copy(update={"stock_research_jobs_enabled": True})
    redis = Redis(host=settings.redis_host, port=settings.redis_port, db=settings.redis_db, socket_timeout=3, socket_connect_timeout=3)
    prefix = "qa_dual_" + uuid4().hex + ":"
    owned_keys = [prefix + quote_key(mode, "rb2701") for mode in ("live", "virtual")]

    class NamespacedRealCache:
        async def mget(self, keys):
            check("valuation_requests_only_known_mode_keys", set(keys) <= {quote_key("live", "rb2701"), quote_key("virtual", "rb2701")})
            return await redis.mget([prefix + key for key in keys])

    try:
        check("real_redis_available", await redis.ping())
        for mode, price, source in (("live", 3010, "sina"), ("virtual", 3020, "ctp:openctp")):
            key = prefix + quote_key(mode, "rb2701")
            check("qa_quote_key_created_without_overwrite", await redis.set(key, json.dumps({"symbol": "rb2701", "last_price": price, "source": source, "recv_ts": time.time()}), nx=True, ex=120))
        application.state.redis = NamespacedRealCache()
        check("account_valuation_requires_jwt", (await client.get("/api/paper/account")).status_code == 401)
        for user_id, mode, balance, floating in ((owner_id, "live", "1111", "100"), (owner_id, "virtual", "7777", "1400"), (other_id, "virtual", "9999", "1800")):
            response = await client.get("/api/paper/account?trading_mode=live", headers=headers(user_id, mode))
            check("valuation_account_http_success_and_trace", response.status_code == 200 and bool(response.headers.get("X-Request-ID")))
            payload = response.json()
            mark = payload["valuation"]
            check("valuation_exact_user_and_jwt_mode", payload["trading_mode"] == mark["trading_mode"] == mode and mark["status"] == "cache_marked")
            check("valuation_decimal_money_formula", Decimal(mark["equity"]) == Decimal(balance) + Decimal(floating) and Decimal(mark["unrealized_pnl"]) == Decimal(floating))
            check("legacy_cash_baseline_unchanged_and_flagged", payload["total_equity"] == float(balance) and payload["equity_basis"] == "book_balance_plus_frozen_excludes_floating" and payload["legacy_pnl_fields_available"] is False)
            check("today_pnl_unknown_not_zero", mark["today_pnl"] is None and mark["method_version"] == "paper-cache-mark-v1")
        await redis.delete(owned_keys[1])
        missing = (await client.get("/api/paper/account", headers=headers(owner_id))).json()["valuation"]
        check("missing_virtual_price_does_not_fallback_to_live", missing["status"] == "unavailable" and all(missing[field] is None for field in ("equity", "unrealized_pnl", "risk_rate")))
        await redis.set(owned_keys[1], json.dumps({"symbol": "rb2701", "last_price": 3020, "source": "sina", "recv_ts": time.time()}), ex=120)
        wrong = (await client.get("/api/paper/account", headers=headers(owner_id))).json()["valuation"]
        check("virtual_rejects_live_source_in_virtual_cache", wrong["status"] == "unavailable")
        await redis.set(owned_keys[1], json.dumps({"symbol": "rb2701", "last_price": 3020, "source": "ctp:openctp", "recv_ts": time.time()}), ex=120)
        async with scoped() as session:
            held_account = await session.scalar(select(PaperAccount).where(PaperAccount.user_id == owner_id, PaperAccount.trading_mode == "virtual"))
            async with scoped() as writer:
                await writer.execute(update(PaperAccount).where(PaperAccount.id == held_account.id).values(balance=Decimal("7777.01"), frozen_margin=Decimal("20.02")))
                await writer.commit()
            valuation = await account_valuation(session, owner_id, {"account_id": str(held_account.id)}, application.state.redis, "virtual")
            check("valuation_refreshes_loaded_account_and_preserves_decimal_cents", valuation.book_equity == Decimal("7797.03") and valuation.equity == Decimal("9197.03"))
        async with scoped() as session:
            await session.execute(update(PaperAccount).where(PaperAccount.user_id == owner_id, PaperAccount.trading_mode == "virtual").values(balance=Decimal("7777"), frozen_margin=Decimal("0")))
            await session.commit()

        async with scoped() as session:
            for table in ("stock_instruments", "kline_history"):
                await session.execute(text(f'CREATE TABLE {table} (LIKE public.{table} INCLUDING ALL)'))
            namespace = await session.scalar(text("SELECT current_schema()"))
            check("research_tables_resolve_to_private_schema", namespace.startswith("qa_completion_b02_") and await session.scalar(text("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:namespace AND c.relname IN ('stock_instruments','kline_history')"), {"namespace": namespace}) == 2)
            dates = pd.bdate_range("2024-01-01", "2024-04-30")
            for position in range(10):
                code = str(600001 + position)
                session.add(StockInstrument(id=position + 1, code=code, name=f"私有测试{position}", exchange="SSE", industry="私有测试行业", updated_at=datetime(2024, 1, 1)))
                for offset, trading_date in enumerate(dates):
                    price = 100 * (1.001 + position * 0.001) ** offset
                    identifier = 1000 + position * len(dates) + offset
                    session.add(KlineHistory(id=identifier, market_type="stock", code=code, trade_date=trading_date.date(), open=price, high=price, low=price, close=price, volume=10000))
                    session.add(StockValuation(id=identifier, code=code, trade_date=trading_date.date(), pb=float(position + 1), total_mv=float(10 + (position + 1) ** 2)))
            session.add(StockResearchSchemaVersion(version=job_api.SCHEMA_VERSION, checksum=hashlib.sha256(job_api.SCHEMA_PATH.read_bytes()).hexdigest()))
            await session.commit()
        parameters = {"factor_id": "pb", "start_date": "20240101", "end_date": "20240430", "groups": 2, "horizon": 5,
                      "preprocess": {"winsorize": "none", "standardize": True, "fillna": True, "industry_neutral": True, "mktcap_neutral": True}}
        response = await client.post("/api/stock/factors/analysis", json=parameters, headers=headers(stock_id))
        check("stock_real_pg_loader_and_v5_engine_http", response.status_code == 200 and response.headers.get("X-Method-Version") == METHOD_VERSION)
        result = response.json()
        check("stock_v5_identifiability_policy_returned", result["neutralization_policy"]["min_residual_degrees_of_freedom"] == 1 and result["neutralization_policy"]["requires_full_column_rank"] is True and result["ic"]["periods"] > 0)
        body = TypeAdapter(CreateResearchJob).validate_python({"kind": "analysis", "idempotency_key": str(uuid4()), "parameters": parameters})
        created = await client.post("/api/stock/research/jobs", json=body.model_dump(mode="json"), headers=headers(stock_id))
        check("stock_http_refuses_creation_without_healthy_worker", created.status_code == 503)
        async with scoped() as session:
            created = await ResearchJobService(session, stock_id, "virtual").create(body, request_id=uuid4().hex)
            await session.commit()
        check("stock_v5_job_created_via_real_service", created["method_version"] == METHOD_VERSION)
        job_id = UUID(created["id"])
        async with scoped() as session:
            claimed = await claim_next_job(session)
            check("stock_job_claimed_through_real_state_machine", claimed is not None and claimed.id == job_id)
            worker_token = claimed.worker_token
            await session.commit()
        fingerprint = hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        manifest = {"data_version": fingerprint, "source": "synthetic_private_schema_fixture_not_market_certification", "immutable_archive": False, "point_in_time_certified": False}
        async with scoped() as session:
            check("stock_real_calculation_result_persisted", await finish_job(session, job_id, worker_token, result=result, manifest=manifest))
            check("stock_worker_slot_released", await release_worker_slot(session, job_id, worker_token))
            await session.commit()
        readback = await client.get(f"/api/stock/research/jobs/{job_id}/result", headers=headers(stock_id))
        check("stock_persisted_v5_result_returned_exactly", readback.status_code == 200 and all(readback.json().get(key) == value for key, value in result.items()) and readback.json()["research_job_id"] == str(job_id) and readback.json()["data_version"] == fingerprint)
        async with scoped() as session:
            await session.execute(update(User).where(User.id == other_id).values(market_type="stock"))
            await session.commit()
        for user_id, mode in ((other_id, "virtual"), (stock_id, "live")):
            forbidden = await client.get(f"/api/stock/research/jobs/{job_id}/result", headers=headers(user_id, mode))
            check("stock_result_wrong_owner_or_mode_404", forbidden.status_code == 404)
        async with scoped() as session:
            await session.execute(update(StockValuation).values(total_mv=1000))
            await session.commit()
        rejected = await client.post("/api/stock/factors/analysis", json=parameters, headers=headers(stock_id))
        check("stock_constant_cap_not_fabricated_as_residual_signal", rejected.status_code == 422 and rejected.headers.get("X-Research-Error") == "insufficient_data")
    finally:
        await redis.delete(*owned_keys)
        check("private_redis_keys_cleaned", not await redis.exists(*owned_keys))
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(harness.main(additional_checks=additional_checks, report_label="dual"))
