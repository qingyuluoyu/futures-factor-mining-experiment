"""Package ⑤ red/green: downstream gates read the graded qualification, QA path unchanged."""
from decimal import Decimal as D
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from tests.test_stock_execution_v2 import bar
from tests.test_stock_research_archive_v2 import prepare, service
from tests.test_stock_research_job_service import job_database
from tests.test_stock_research_panels_v2 import snapshot


def portfolio_request(**changes):
    from app.schemas.stock_portfolio_v2 import PortfolioRequest
    return PortfolioRequest.model_validate({"scope": {"owner_id": str(UUID(int=1)), "trading_mode": "virtual"},
        "dataset": {"dataset_id": str(UUID(int=2)), "data_version": "a" * 64, "qualification": "observation_only"},
        "pool_version": "qa-pool-v1", "start": "2024-01-02", "end": "2024-01-05", "top_n": 1,
        "rebalance": "explicit", "initial_cash": "10000", "qa_only": True,
        "costs": {"slippage_rate": "0"}, **changes})


def portfolio_inputs():
    from app.schemas.stock_portfolio_v2 import PortfolioInputs
    dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
    evidence = {name: "qa:complete-coverage" for name in ["calendar_reference", "raw_prices_reference",
        "status_and_limits_reference", "corporate_actions_reference", "delistings_reference", "historical_universe_reference"]}
    evidence.update(coverage_start=dates[0], coverage_end=dates[-1])
    return PortfolioInputs.model_validate({"calendar": dates, "signals": [
        {"decision_date": dates[0], "signal_version": "qa-score-v1", "candidates": [
            {"instrument_id": "000001.SZ", "score": "2", "marketcap": "100"},
            {"instrument_id": "600000.SH", "score": "1", "marketcap": "300"}]}],
        "bars": [bar(day, instrument) for day in dates for instrument in ["000001.SZ", "600000.SH"]],
        "evidence": evidence, "events": []})


def test_qa_portfolio_path_is_preserved_byte_for_byte_in_behaviour():
    from app.services.stock_research_v2.portfolio import simulate_portfolio
    result = simulate_portfolio(portfolio_request(), portfolio_inputs())
    assert result.status == "completed" and result.validation_status == "qa_only"
    assert result.blocking_reasons == []
    assert len(result.daily) == 4 and result.daily[1].equity == D("9994.91")


def test_observation_dataset_still_cannot_gain_production_qualification_from_evidence_fields():
    from app.services.stock_research_v2.portfolio import simulate_portfolio
    result = simulate_portfolio(portfolio_request(qa_only=False), portfolio_inputs())
    assert result.status == "not_qualified" and "dataset_not_backtest_ready" in result.blocking_reasons


def test_backtest_ready_dataset_passes_the_portfolio_gate_without_qa_mode():
    from app.services.stock_research_v2.portfolio import simulate_portfolio
    result = simulate_portfolio(portfolio_request(qa_only=False, dataset={
        "dataset_id": str(UUID(int=2)), "data_version": "a" * 64, "qualification": "backtest_ready"}), portfolio_inputs())
    assert "dataset_not_backtest_ready" not in result.blocking_reasons
    assert result.validation_status == "execution_calculation_only"
    assert result.status == "completed"


def test_research_only_dataset_still_cannot_run_a_tradable_backtest():
    from app.services.stock_research_v2.portfolio import simulate_portfolio
    result = simulate_portfolio(portfolio_request(qa_only=False, dataset={
        "dataset_id": str(UUID(int=2)), "data_version": "a" * 64, "qualification": "research_only"}), portfolio_inputs())
    assert "dataset_not_backtest_ready" in result.blocking_reasons


def test_gates_share_one_capability_authority():
    from app.services.stock_research_v2.qualification import allows
    assert allows("observation_only", "analysis") is False
    assert allows("research_only", "analysis") is True
    assert allows("backtest_ready", "analysis") is True
    assert allows("research_only", "portfolio") is False
    assert allows("backtest_ready", "portfolio") is True
    assert allows("backtest_ready", "unknown_capability") is False


def test_ml_gate_reads_the_grade_and_qa_mode_still_works():
    from app.schemas.stock_research_v2 import DatasetRef
    from app.services.stock_research_jobs import JobError
    from app.services.stock_research_v2.ml_train import fit_ranker
    from tests.test_stock_ml_train import arguments
    fold, options = arguments()
    options["qa_only"] = False
    with pytest.raises(JobError) as blocked:
        fit_ranker(fold, **options)
    assert blocked.value.code == "dataset_not_qualified"
    options["dataset"] = DatasetRef(dataset_id=uuid4(), data_version="a" * 64, qualification="research_only")
    assert fit_ranker(fold, **options).manifest.validation_status == "research_only"
    options["qa_only"] = True
    options["dataset"] = DatasetRef(dataset_id=uuid4(), data_version="a" * 64, qualification="observation_only")
    assert fit_ranker(fold, **options).manifest.validation_status == "qa_only"


def test_experiment_gate_reads_the_grade_and_qa_mode_still_works(tmp_path):
    from app.schemas.stock_research_v2 import DatasetRef, Scope
    from app.services.stock_research_jobs import JobError
    from app.services.stock_research_v2.artifacts import ArtifactStore
    from app.services.stock_research_v2.factor_values import builtin_factor
    from app.services.stock_research_v2.research_experiment import run_experiment
    from tests.test_stock_factor_values_v2 import config, custom, frames
    from tests.test_stock_ml_dataset import split_spec
    data = frames()
    fixed, _ = custom()
    factors = [builtin_factor("roe"), builtin_factor("pb"), fixed]
    scope = Scope(owner_id=uuid4(), trading_mode="virtual")
    reference = DatasetRef(dataset_id=uuid4(), data_version="a" * 64, qualification="observation_only")
    store = ArtifactStore(tmp_path)
    kwargs = dict(scope=scope, dataset=reference, split=split_spec(data), horizon=3, groups=5, top_n=5, config=config(), store=store,
        training_parameters={"n_estimators": 30, "max_depth": 4, "num_leaves": 15, "min_child_samples": 5})
    with pytest.raises(JobError) as blocked:
        run_experiment(data, factors, qa_only=False, **kwargs)
    assert blocked.value.code == "dataset_not_qualified"
    upgraded = reference.model_copy(update={"qualification": "research_only"})
    report = run_experiment(data, factors, qa_only=False, **{**kwargs, "dataset": upgraded})
    assert report["validation_status"] == "calculation_only_unverified_inputs"
    qa = run_experiment(data, factors, qa_only=True, **kwargs)
    assert qa["validation_status"] == "qa_only"


@pytest.mark.asyncio
async def test_archived_study_gate_reads_the_grade_and_qa_mode_still_works(tmp_path):
    from app.services.stock_research_jobs import JobError
    from app.services.stock_research_v2.archived_study import run_archived_study
    from tests.test_stock_factor_values_v2 import config
    from tests.test_stock_ml_dataset import split_spec
    from tests.test_stock_study_inputs import archived_inputs
    async with archived_inputs(tmp_path) as (registry, receipt, calendar, universe, data, _):
        options = dict(calendar=calendar, universe=universe, builtin_ids=["roe", "pb"], custom_refs=[], split=split_spec(data),
            horizon=3, groups=5, top_n=5, config=config(), request_id="d" * 32,
            training_parameters={"n_estimators": 20, "max_depth": 4, "num_leaves": 15, "min_child_samples": 5})
        with pytest.raises(JobError) as gated:
            await run_archived_study(registry, receipt, **options)
        assert gated.value.code == "dataset_not_qualified"
        first = await run_archived_study(registry, receipt, qa_only=True, **options)
        assert first["study"]["model"]["runtime"]["lightgbm"] == "4.7.0"
        assert first["inputs"]["research_enabled"] is False


def _application(factory, owner, store=None):
    from fastapi import FastAPI

    from app.api.stock_research_v2 import router
    from app.core.deps import AuthContext, get_auth_context, get_session
    app = FastAPI()
    app.include_router(router, prefix="/api/stock")
    app.state.stock_research_artifact_store = store
    context = SimpleNamespace(user=SimpleNamespace(id=owner, market_type="stock"), mode="virtual")
    app.dependency_overrides[get_auth_context] = lambda: AuthContext(context.user, context.mode)

    async def sessions():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = sessions
    return app, context


@pytest.mark.asyncio
async def test_availability_and_description_report_real_computed_booleans(tmp_path):
    import httpx

    from app.schemas.stock_dataset_inspection import DatasetAvailability, DatasetDescription
    from app.services.stock_research_v2.artifacts import ArtifactStore
    async with job_database() as (factory, owner, other):
        app, _ = _application(factory, owner)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            unavailable = (await client.get("/api/stock/research/v2/availability")).json()
            assert unavailable["catalog_enabled"] is False
            assert unavailable["inspection_enabled"] is False
            assert unavailable["research_enabled"] is False and unavailable["parquet_enabled"] is False
        await prepare(factory)
        async with factory() as session:
            ref, source = await snapshot(service(session, owner, tmp_path / "artifacts"))
        app, _ = _application(factory, owner, ArtifactStore(tmp_path / "artifacts"))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            available = (await client.get("/api/stock/research/v2/availability")).json()
            assert available["catalog_enabled"] is True and available["detail_enabled"] is True
            # migration 020 is not applied in this fixture, so inspection stays refused
            assert available["inspection_enabled"] is False
            assert available["research_enabled"] is False and available["parquet_enabled"] is False
            detail = (await client.get(f"/api/stock/research/v2/datasets/{ref.dataset_id}?data_version={ref.data_version}")).json()
            assert detail["research_enabled"] is False
            assert detail["reference"]["qualification"] == "observation_only"
    assert DatasetAvailability.model_validate({"catalog_enabled": True, "detail_enabled": True, "inspection_enabled": False,
        "research_enabled": False, "parquet_enabled": False, "reason": "x"}).catalog_enabled is True
    assert DatasetDescription.model_validate({"reference": {"dataset_id": str(uuid4()), "data_version": "a" * 64, "qualification": "research_only"},
        "manifest_version": "stock-observation-manifest-v1", "start": "2026-01-05", "end": "2026-01-06", "warmup_start": "2026-01-05",
        "pool_id": "qa", "pool_version": "a" * 64, "instruments": ["000001.SZ"], "sources": [{"source_id": str(uuid4()),
        "sha256": "a" * 64, "byte_size": 10, "row_count": 1, "kind": "daily"}], "integrity": "manifest_verified_sources_not_checked",
        "research_enabled": True, "limitations": []}).research_enabled is True
