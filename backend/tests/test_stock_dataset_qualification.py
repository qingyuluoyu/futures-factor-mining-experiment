from uuid import UUID, uuid4

import pytest

from tests.test_stock_research_panels_v2 import snapshot
from tests.test_stock_research_archive_v2 import prepare, service
from tests.test_stock_research_job_service import job_database


@pytest.mark.asyncio
@pytest.mark.parametrize("basis,date_only", [("vendor_reported", False), ("parent", False), ("consolidated", True)])
async def test_unresolved_financial_basis_or_date_only_time_is_not_an_eligible_dependency(tmp_path, basis, date_only):
    from app.schemas.stock_dataset_inspection import ResearchRequirements
    from app.services.stock_research_v2.qualification import inspect_requirements
    from tests.test_stock_research_archive_v2 import page
    observation = {"instrument_id": "000001.SZ", "effective_date": "2026-01-05", "report_period": "2025-09-30", "announced_at": "2026-01-05T01:00:00Z", "available_at": "2026-01-05T01:00:00Z", "vintage": "original_filing", "vintage_reference": "qa-not-live-proof", "values": {"roe": "0.12"}}
    if date_only:
        observation.pop("announced_at")
        observation["announced_on"] = "2026-01-05"
    financial = page(kind="financial", price_adjustment="not_applicable", financial_basis=basis, frequency="disclosure", units={"roe": "percent"}, observations=[observation])
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        async with factory() as session:
            archive = service(session, owner, tmp_path / "artifacts")
            ref, source = await snapshot(archive, extra=financial)
            result = await inspect_requirements(archive, ref, ResearchRequirements(factor_ids=["roe"]))
            assert result["fields"]["roe"]["raw_observations"] == 1
            assert result["fields"]["roe"]["eligible_observations"] == 0
            assert result["missing_fields"] == ["roe"] and result["research_enabled"] is False


@pytest.mark.asyncio
async def test_momentum_does_not_require_financial_fields_and_does_not_claim_unverified_calendar(tmp_path):
    from app.schemas.stock_dataset_inspection import ResearchRequirements
    from app.services.stock_research_v2.qualification import inspect_requirements
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        async with factory() as session:
            archive = service(session, owner, tmp_path / "artifacts")
            ref, source = await snapshot(archive)
            result = await inspect_requirements(archive, ref, ResearchRequirements(factor_ids=["ret_20d"]))
            assert result["required_fields"] == ["close"]
            assert result["missing_fields"] == []
            assert result["research_enabled"] is False
            assert "calendar_unverified" in result["blocking_reasons"]
            assert result["qualification"] == "observation_only"


@pytest.mark.asyncio
async def test_selected_financial_factor_missing_is_not_silently_dropped_and_rotation_requires_industry(tmp_path):
    from app.schemas.stock_dataset_inspection import ResearchRequirements
    from app.services.stock_research_v2.qualification import inspect_requirements
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        async with factory() as session:
            archive = service(session, owner, tmp_path / "artifacts")
            ref, source = await snapshot(archive)
            financial = await inspect_requirements(archive, ref, ResearchRequirements(factor_ids=["ret_20d", "roe"]))
            assert financial["missing_fields"] == ["roe"]
            assert financial["required_fields"] == ["close", "roe"]
            rotation = await inspect_requirements(archive, ref, ResearchRequirements(factor_ids=["ret_20d"], action="rotation"))
            assert "historical_industry_unavailable" in rotation["blocking_reasons"]
            weighted = await inspect_requirements(archive, ref, ResearchRequirements(factor_ids=["ret_20d"], weight="mktcap"))
            assert "marketcap" in weighted["missing_fields"]


@pytest.mark.asyncio
async def test_saved_custom_factor_dependencies_resolve_exact_owned_version_before_data_inspection(tmp_path):
    from app.schemas.stock_dataset_inspection import ResearchRequirements
    from app.schemas.stock_custom_factor import CustomFactorCreate
    from app.services.stock_research_v2.custom_factors import CustomFactorService
    from app.services.stock_research_v2.qualification import inspect_requirements
    from app.services.stock_research_jobs import JobError
    from tests.test_stock_custom_factors_api import prepare as prepare_factors
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        await prepare_factors(factory)
        async with factory() as session:
            factors = CustomFactorService(session, owner, "virtual")
            saved = await factors.mutate(CustomFactorCreate(idempotency_key=uuid4(), name="自定义动量", expression="close/lag(close,20)-1", direction="high"), request_id="a" * 32)
            await session.commit()
            archive = service(session, owner, tmp_path / "artifacts")
            ref, source = await snapshot(archive)
            fixed = {"id": saved["id"], "revision": saved["revision"], "expression_version": saved["expression_version"]}
            request = ResearchRequirements(factor_ids=[], custom_factors=[fixed])
            result = await inspect_requirements(archive, ref, request)
            assert result["required_fields"] == ["close"]
            assert result["lookback"] == 20
            assert result["custom_factors"][0]["expression_version"] == saved["expression_version"]
            changed = ResearchRequirements(factor_ids=[], custom_factors=[{**fixed, "expression_version": "f" * 64}])
            with pytest.raises(JobError) as conflict:
                await inspect_requirements(archive, ref, changed)
            assert conflict.value.status == 409
            with pytest.raises(JobError) as denied:
                await inspect_requirements(service(session, other, tmp_path / "artifacts"), ref, request)
            assert denied.value.status == 404
