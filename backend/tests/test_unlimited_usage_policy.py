from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.quota.quota_svc import FEATURE_BACKTEST, FEATURE_FACTOR, check_and_incr, get_effective_limits
from app.services.system_settings_svc import settings_to_dict, update_system_settings


@pytest.mark.parametrize("role", ["user", "admin"])
@pytest.mark.parametrize("override", [None, 1, 9])
async def test_daily_usage_is_unlimited_despite_legacy_global_or_user_overrides(monkeypatch, role, override):
    settings = SimpleNamespace(max_tasks_per_user=5, factor_lab_daily_limit=3, backtest_daily_limit=2)
    monkeypatch.setattr("app.services.system_settings_svc.ensure_system_settings", AsyncMock(return_value=settings))
    user = SimpleNamespace(id=uuid4(), role=role, max_tasks_override=2, factor_lab_daily_override=override, backtest_daily_override=override)
    limits = await get_effective_limits(object(), user)
    assert limits["factor_daily"] is None
    assert limits["backtest_daily"] is None
    assert limits["max_tasks"] == (None if role == "admin" else 2)


@pytest.mark.parametrize("feature", [FEATURE_FACTOR, FEATURE_BACKTEST])
async def test_old_callers_cannot_restore_daily_denial_even_with_exhausted_redis(feature):
    redis = SimpleNamespace(get=AsyncMock(return_value=b"1000000"), incr=AsyncMock(), expire=AsyncMock())
    user = SimpleNamespace(id=uuid4(), role="user")
    for _ in range(6):
        assert await check_and_incr(redis, user, feature, 1, "旧版调用") == 0
    redis.get.assert_not_awaited()
    redis.incr.assert_not_awaited()
    redis.expire.assert_not_awaited()


def legacy_settings():
    return SimpleNamespace(registration_enabled=True, daily_register_limit=100, register_reset_hour=6,
                           paper_claim_amount=10000, paper_claim_period="monthly", paper_claim_reset_hour=6,
                           paper_total_claim_cap=0, system_name="QA", factor_lab_daily_limit=3,
                           backtest_daily_limit=2, max_tasks_per_user=5, updated_at=None)


def test_admin_settings_report_effective_unlimited_without_mutating_legacy_row():
    row = legacy_settings()
    payload = settings_to_dict(row)
    assert payload["factor_lab_daily_limit"] == 0
    assert payload["backtest_daily_limit"] == 0
    assert row.factor_lab_daily_limit == 3 and row.backtest_daily_limit == 2


async def test_legacy_admin_updates_cannot_reintroduce_daily_limits(monkeypatch):
    row = legacy_settings()
    monkeypatch.setattr("app.services.system_settings_svc.ensure_system_settings", AsyncMock(return_value=row))
    session = SimpleNamespace(flush=AsyncMock())
    result = await update_system_settings(session, {"factor_lab_daily_limit": 5, "backtest_daily_limit": 3, "max_tasks_per_user": 4})
    assert result.factor_lab_daily_limit == result.backtest_daily_limit == 0
    assert result.max_tasks_per_user == 4
    session.flush.assert_awaited_once()


async def test_stock_research_creation_has_no_lifetime_count_quota_but_keeps_active_backpressure():
    from datetime import datetime, timezone
    from app.models.stock_research_job import StockResearchJob
    from app.services.stock_research_jobs import JobError, ResearchJobService
    from tests.test_stock_research_job_service import creation, job_database

    now = datetime.now(timezone.utc)
    async with job_database() as (factory, owner, other), factory() as session:
        for _ in range(1000):
            session.add(StockResearchJob(id=uuid4(), user_id=owner, trading_mode="virtual", kind="analysis", title="历史取消任务夹具", status="cancelled", step="cancelled", progress=100, request_id="q" * 32, idempotency_key=uuid4(), request_hash="a" * 64, params_json={}, method_version="legacy-fixture", workflow_version="legacy-fixture", data_manifest={}, result_bytes=0, created_at=now, updated_at=now, finished_at=now))
        await session.commit()
        service = ResearchJobService(session, owner, "virtual")
        created = await service.create(creation(), request_id="b" * 32)
        assert created["status"] == "pending"
        await session.commit()
        with pytest.raises(JobError) as busy:
            await service.create(creation(), request_id="c" * 32)
        assert busy.value.code == "user_queue_full"
