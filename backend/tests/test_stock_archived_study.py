import pytest

from app.services.stock_research_jobs import JobError
from tests.test_stock_study_inputs import archived_inputs
from tests.test_stock_ml_dataset import split_spec
from tests.test_stock_factor_values_v2 import config


@pytest.mark.asyncio
async def test_private_registry_parquet_pit_to_real_ml_and_saved_report_stays_qa_only(tmp_path):
    from app.services.stock_research_v2.archived_study import run_archived_study
    async with archived_inputs(tmp_path) as (registry,receipt,calendar,universe,data,_):
        options=dict(calendar=calendar,universe=universe,builtin_ids=["roe","pb"],custom_refs=[],split=split_spec(data),
            horizon=3,groups=5,top_n=5,config=config(),request_id="d"*32,training_parameters={"n_estimators":20,"max_depth":4,"num_leaves":15,"min_child_samples":5})
        with pytest.raises(JobError) as gated:
            await run_archived_study(registry,receipt,**options)
        assert gated.value.code == "dataset_not_qualified"
        first=await run_archived_study(registry,receipt,qa_only=True,**options)
        second=await run_archived_study(registry,receipt,qa_only=True,**options)
        assert first["semantic_sha256"] == second["semantic_sha256"]
        assert first["study"]["model"]["runtime"]["lightgbm"] == "4.7.0"
        assert first["study"]["candidate_count"] == 900 and first["inputs"]["research_enabled"] is False
        assert first["study"]["portfolio_backtest"]["available"] is False
        assert registry.archive.require_store().read_json(first["saved_report"]["sha256"])["inputs"] == first["inputs"]
