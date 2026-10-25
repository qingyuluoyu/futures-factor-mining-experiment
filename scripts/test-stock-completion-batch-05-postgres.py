import asyncio
from datetime import date, datetime, timedelta, timezone
import importlib.util
import json
import math
from pathlib import Path
import re
from uuid import uuid4
from unittest.mock import patch

from redis.asyncio import Redis
from redis.backoff import NoBackoff
from redis.retry import Retry
from sqlalchemy import select, text


ROOT = Path(__file__).resolve().parents[1]
specification = importlib.util.spec_from_file_location("completion_pg_harness", ROOT / "scripts/test-stock-completion-batch-02-postgres.py")
harness = importlib.util.module_from_spec(specification)
specification.loader.exec_module(harness)

from app.api.admin import router as admin_router
from app.api.backtest import router as backtest_router
from app.api.factor_lab import router as factor_router
from app.api.factor_mining import router as mining_router
from app.models.kline_history import KlineHistory
from app.models.stock_research_job import StockResearchJob
from app.models.system_settings import SystemSettings
from app.models.user import User
from app.services.quota.quota_svc import FEATURE_BACKTEST, FEATURE_FACTOR, _quota_key, get_effective_limits
from app.services.stock_research_jobs import JobError, ResearchJobService
from tests.test_stock_research_job_service import creation


async def additional_checks(*, check, scoped, application, client, headers, owner_id, other_id, stock_id, settings):
    for router in (admin_router, backtest_router, factor_router, mining_router):
        application.include_router(router)
    async with scoped() as session:
        namespace = await session.scalar(text("SELECT current_schema()"))
        if not re.fullmatch(r"qa_completion_b02_[a-f0-9]{32}", namespace):
            raise RuntimeError("Invalid isolated namespace")
        for table in ("kline_history", "ai_trading_tasks"):
            await session.execute(text(f'CREATE TABLE "{namespace}".{table} (LIKE public.{table} INCLUDING ALL)'))
        owner = await session.get(User, owner_id)
        owner.factor_lab_daily_override = owner.backtest_daily_override = 1
        owner.max_tasks_override = 2
        row = await session.get(SystemSettings, 1)
        row.factor_lab_daily_limit = row.backtest_daily_limit = 1
        for index in range(240):
            price = 3000 + index * 0.3 + math.sin(index / 7) * 12
            session.add(KlineHistory(id=index + 1, market_type="futures", code="RB", trade_date=date(2026, 1, 1) + timedelta(days=index), open=price, high=price + 3, low=price - 3, close=price + math.sin(index / 5), volume=10000, hold=1000))
        session.add(KlineHistory(id=241, market_type="futures", code="300308.SZ", trade_date=date(2026, 7, 1), open=100, high=101, low=99, close=100, volume=1000, hold=0))
        await session.commit()
        limits = await get_effective_limits(session, owner)
        check("b05_positive_legacy_user_global_limits_are_ineffective", limits == {"max_tasks": 2, "factor_daily": None, "backtest_daily": None})

    if settings.redis_host not in ("127.0.0.1", "localhost") or settings.redis_port != 6380:
        raise RuntimeError("Only dedicated local Redis6380 permitted")
    redis = Redis(host=settings.redis_host, port=settings.redis_port, db=settings.redis_db, protocol=2,
                  socket_connect_timeout=2, socket_timeout=2, retry=Retry(NoBackoff(), 0))
    keys = [_quota_key(owner_id, feature) for feature in (FEATURE_FACTOR, FEATURE_BACKTEST)]
    previous_redis = getattr(application.state, "redis", None)
    created_keys = []

    async def private_bars(_redis, symbol, timeframe, *, session, limit=200, **options):
        if symbol.lower() != "rb2701" or timeframe != "1d":
            raise ValueError("Fixture only supports explicit RB daily series")
        rows = (await session.execute(select(KlineHistory).where(KlineHistory.code == "RB", KlineHistory.market_type == "futures").order_by(KlineHistory.trade_date))).scalars().all()
        return [{"time": row.trade_date.isoformat(), "open": row.open, "high": row.high, "low": row.low, "close": row.close, "volume": row.volume, "hold": row.hold} for row in rows][-limit:]

    try:
        check("b05_real_redis_available", await redis.ping())
        for key in keys:
            check("b05_qa_redis_key_is_new", not await redis.exists(key))
            await redis.set(key, "1000000", ex=120)
            created_keys.append(key)
        application.state.redis = redis
        payload = {"symbol": "rb2701", "timeframe": "1d", "strategy_type": "ma_cross", "start_date": "2026-06-01", "end_date": "2026-08-15", "initial_cash": 1000000}
        factor = {"symbol": "rb2701", "timeframe": "1d", "factor_tokens": [8], "cost": 0.0001, "walk_forward_folds": 0}
        with patch("app.services.backtest.data.get_kline_history", side_effect=private_bars), patch("app.api.factor_lab.common.get_kline_history", side_effect=private_bars):
            for iteration in range(3):
                response = await client.post("/api/backtest/run", json=payload, headers=headers(owner_id))
                check(f"b05_backtest_{iteration}_runs_real_kernel_after_exhausted_daily_limit", response.status_code == 200 and response.json()["metrics"] and response.json()["config"]["ai_calls"] == 0)
                response = await client.post("/api/factor-lab/backtest-factor", json=factor, headers=headers(owner_id))
                check(f"b05_factor_{iteration}_runs_real_kernel_after_exhausted_daily_limit", response.status_code == 200 and response.json()["bars"] == 240 and bool(response.json()["equity_curve"]))
        check("b05_anonymous_backtest_still_requires_auth", (await client.post("/api/backtest/run", json=payload)).status_code == 401)
        check("b05_invalid_backtest_input_still_rejected", (await client.post("/api/backtest/run", json={**payload, "timeframe": "invalid"}, headers=headers(owner_id))).status_code == 422)
        response = await client.get("/api/factor-mining/symbols", headers=headers(owner_id))
        check("b05_mislabeled_stock_is_hidden_without_data_rewrite", response.status_code == 200 and [item["code"] for item in response.json()["items"]] == ["RB"])
        async with scoped() as session:
            check("b05_mislabeled_price_is_preserved_for_audited_migration", await session.scalar(select(KlineHistory.code).where(KlineHistory.id == 241)) == "300308.SZ")
            owner = await session.get(User, owner_id)
            owner.role = "admin"
            await session.commit()
        response = await client.put("/api/admin/settings", json={"factor_lab_daily_limit": 9, "backtest_daily_limit": 9}, headers=headers(owner_id))
        check("b05_legacy_admin_payload_cannot_enable_daily_limits", response.status_code == 200 and response.json()["factor_lab_daily_limit"] == response.json()["backtest_daily_limit"] == 0)
        response = await client.get(f"/api/admin/users/{owner_id}/quota", headers=headers(owner_id))
        check("b05_admin_effective_limits_are_unlimited", response.status_code == 200 and response.json()["effective"]["factor_daily"] is None and response.json()["effective"]["backtest_daily"] is None)
        check("b05_regular_user_cannot_modify_admin_policy", (await client.put("/api/admin/settings", json={"factor_lab_daily_limit": 3}, headers=headers(other_id))).status_code == 403)
        for key in keys:
            check("b05_legacy_redis_count_not_read_or_consumed", await redis.get(key) == b"1000000")
        now = datetime.now(timezone.utc)
        async with scoped() as session:
            session.add_all([StockResearchJob(id=uuid4(), user_id=stock_id, trading_mode="virtual", kind="analysis", title="历史取消任务夹具", status="cancelled", step="cancelled", progress=100, request_id=uuid4().hex, idempotency_key=uuid4(), request_hash="a" * 64, params_json={}, method_version="legacy-fixture", workflow_version="legacy-fixture", data_manifest={}, result_bytes=0, created_at=now, updated_at=now, finished_at=now) for _ in range(1000)])
            await session.commit()
            service = ResearchJobService(session, stock_id, "virtual")
            created = await service.create(creation(), request_id=uuid4().hex)
            await session.commit()
            check("b05_stock_1001st_research_is_accepted", created["status"] == "pending")
            try:
                await service.create(creation(), request_id=uuid4().hex)
            except JobError as failure:
                check("b05_active_research_backpressure_kept", failure.code == "user_queue_full" and failure.status == 429)
            else:
                check("b05_active_research_backpressure_kept", False)
    finally:
        application.state.redis = previous_redis
        if created_keys:
            await redis.delete(*created_keys)
            check("b05_qa_redis_keys_cleaned", not await redis.exists(*created_keys))
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(harness.main(additional_checks=additional_checks, report_label="b05"))
