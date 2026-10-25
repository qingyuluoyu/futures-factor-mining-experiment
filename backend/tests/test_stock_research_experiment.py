from uuid import uuid4

import pytest

from app.schemas.stock_research_v2 import Scope, DatasetRef
from app.services.stock_research_jobs import JobError
from app.services.stock_research_v2.artifacts import ArtifactStore
from tests.test_stock_factor_values_v2 import frames, custom, config
from tests.test_stock_ml_dataset import split_spec


def test_custom_factor_ic_training_prediction_and_saved_model_form_one_reproducible_run(tmp_path):
    from app.services.stock_research_v2.factor_values import builtin_factor
    from app.services.stock_research_v2.research_experiment import run_experiment, load_experiment_model
    data = frames(); fixed, _ = custom()
    factors = [builtin_factor("roe"), builtin_factor("pb"), fixed]
    scope = Scope(owner_id=uuid4(), trading_mode="virtual")
    reference = DatasetRef(dataset_id=uuid4(), data_version="a"*64, qualification="observation_only")
    store = ArtifactStore(tmp_path)
    kwargs = dict(scope=scope, dataset=reference, split=split_spec(data), horizon=3, groups=5, top_n=5, config=config(), qa_only=True, store=store,
        training_parameters={"n_estimators": 30, "max_depth": 4, "num_leaves": 15, "min_child_samples": 5})
    first = run_experiment(data, factors, **kwargs)
    second = run_experiment(data, factors, **kwargs)
    assert first["semantic_sha256"] == second["semantic_sha256"]
    assert first["factor_analysis"][fixed.key]["ic"]["rank_ic_mean"] > 0.9
    assert len(first["predictions"]) == 30 * 30 and len(first["selected_candidates"]) == 30 * 5
    assert first["model"]["validation_status"] == "qa_only"
    recovered = load_experiment_model(store, first["saved_model"]["sha256"], scope=scope, dataset=reference)
    assert recovered.manifest.model_sha256 == first["model"]["model_sha256"]
    with pytest.raises(JobError): load_experiment_model(store, first["saved_model"]["sha256"], scope=Scope(owner_id=uuid4(), trading_mode="virtual"), dataset=reference)
    assert first["portfolio_backtest"]["available"] is False
    assert first["preprocessing"]["standardize"] is False
    assert first["label_basis"] == "price_return_next_close_no_corporate_actions_unverified"
    assert first["factor_analysis_period"] == {"start": str(data["close"].index[0].date()), "end": str(data["close"].index[39].date())}
    changed = run_experiment(data, factors, **{**kwargs, "config": config(standardize=True)})
    assert changed["model"]["feature_versions"] != first["model"]["feature_versions"]
