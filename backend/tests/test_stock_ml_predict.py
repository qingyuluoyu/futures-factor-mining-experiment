from uuid import uuid4

import pytest

from app.schemas.stock_research_v2 import Scope, DatasetRef
from tests.test_stock_ml_dataset import dataset
from tests.test_stock_ml_split import plan


def options():
    return {"scope": Scope(owner_id=uuid4(), trading_mode="virtual"),
        "dataset": DatasetRef(dataset_id=uuid4(), data_version="a"*64, qualification="observation_only"),
        "feature_versions": {"quality": "b"*64, "value": "c"*64}, "qa_only": True,
        "parameters": {"n_estimators": 15, "max_depth": 3, "num_leaves": 7, "min_child_samples": 5}}


def test_real_two_fold_training_saves_models_and_oos_candidates_before_separate_evaluation(tmp_path):
    from app.services.stock_research_v2.ml_predict import run_walk_forward_experiment, evaluate_walk_forward
    from app.services.stock_research_v2.artifacts import ArtifactStore
    samples, data, _, _ = dataset()
    kwargs = options()
    store = ArtifactStore(tmp_path)
    result = run_walk_forward_experiment(samples, plan(data, fold_count=2), **kwargs, store=store)
    assert len(result["folds"]) == 2
    assert result["evaluation"] is None
    assert result["holdout_registry_verified"] is False
    assert result["score_semantics"] == "ranking_score_not_probability_or_expected_return"
    for fold in result["folds"]:
        assert fold["model"]["runtime"]["lightgbm"] == "4.7.0"
        assert fold["model"]["training_knowledge_cutoff"] < fold["split"]["test_start"]
        assert len(fold["saved_model"]["sha256"]) == 64
        assert len(fold["predictions"]) == 300
        assert fold["selected_candidates"]
    evaluated = evaluate_walk_forward(result, samples, scope=kwargs["scope"], dataset=kwargs["dataset"])
    assert evaluated["counts"]["candidates"] == 600
    assert len(evaluated["fold_evaluations"]) == 2
    recovered = store.read_json(result["saved_report"]["sha256"])["report"]
    assert evaluate_walk_forward(recovered, samples, scope=kwargs["scope"], dataset=kwargs["dataset"]) == evaluated


def test_test_labels_cannot_change_models_scores_or_selection_and_cancel_stops():
    from app.services.stock_research_v2.ml_predict import run_walk_forward_experiment
    from app.services.stock_research_jobs import JobError
    samples, data, _, _ = dataset(); kwargs = options()
    # The terminal test is never an input to the model or parameter selection.
    first = run_walk_forward_experiment(samples, plan(data, fold_count=2), **kwargs)
    samples.candidates.loc[samples.candidates.decision_date.ge(data["close"].index[58]), ["target_return", "relevance"]] = -1
    second = run_walk_forward_experiment(samples, plan(data, fold_count=2), **kwargs)
    for left, right in zip(first["folds"], second["folds"]):
        assert left["model"]["model_sha256"] == right["model"]["model_sha256"]
        assert left["predictions"] == right["predictions"]
    with pytest.raises(JobError): run_walk_forward_experiment(samples, plan(data, fold_count=2), **kwargs, cancel_check=lambda: True)


def test_evaluation_rechecks_scope_frozen_samples_and_training_fingerprints():
    from copy import deepcopy
    from app.services.stock_research_v2.ml_predict import run_walk_forward_experiment, evaluate_walk_forward
    from app.services.stock_research_jobs import JobError
    samples, data, _, _ = dataset(); kwargs = options()
    result = run_walk_forward_experiment(samples, plan(data, fold_count=2), **kwargs)
    identity = {name: kwargs[name] for name in ["scope", "dataset"]}
    with pytest.raises(JobError):
        evaluate_walk_forward(result, samples, **{**identity, "scope": Scope(owner_id=uuid4(), trading_mode="virtual")})
    for change in ["parameters", "score", "training_sha256", "feature_order"]:
        corrupt = deepcopy(result)
        if change == "parameters": corrupt["parameters"]["seed"] = 9
        elif change == "score": corrupt["folds"][0]["predictions"][0]["score"] = 9.
        elif change == "feature_order": corrupt["folds"][0]["model"]["feature_order"].reverse()
        else: corrupt["folds"][0]["model"][change] = "0"*64
        with pytest.raises(JobError): evaluate_walk_forward(corrupt, samples, **identity)
    samples.candidates.loc[0, "quality"] += 1
    # Mutation outside every fold (the warmup) cannot alter evaluation identity.
    assert evaluate_walk_forward(result, samples, **identity)["counts"]["candidates"] == 600
    samples.candidates.loc[100, "quality"] += 1
    with pytest.raises(JobError): evaluate_walk_forward(result, samples, **identity)


@pytest.mark.asyncio
async def test_two_real_folds_and_separate_evaluation_fit_existing_windows_job(tmp_path):
    import json
    import os
    from pathlib import Path
    import sys
    from app.services.stock_research_v2.windows_job_process import spawn_bounded_process
    from app.services.stock_research_worker import supervise_process
    root = Path(__file__).resolve().parents[2]
    code = "\n".join([
        "import json",
        "from pathlib import Path",
        "from tests.test_stock_ml_dataset import dataset",
        "from tests.test_stock_ml_split import plan",
        "from tests.test_stock_ml_predict import options",
        "from app.services.stock_research_v2.artifacts import ArtifactStore",
        "from app.services.stock_research_v2.ml_predict import run_walk_forward_experiment,evaluate_walk_forward",
        "samples,data,_,_=dataset(); args=options()",
        f"store=ArtifactStore(Path({str(tmp_path / 'artifacts')!r}))",
        "report=run_walk_forward_experiment(samples,plan(data,fold_count=2),**args,store=store)",
        "evaluation=evaluate_walk_forward(report,samples,scope=args['scope'],dataset=args['dataset'])",
        f"Path({str(tmp_path / 'qa-report.json')!r}).write_text(json.dumps({{'experiment':report,'evaluation':evaluation}},allow_nan=False),encoding='utf-8')",
        "print(json.dumps({'type':'result','result':{'folds':len(report['folds']),'candidates':evaluation['counts']['candidates'],'saved_report':report['saved_report']}}),flush=True)",
    ])
    environment = {**os.environ, "PYTHONPATH": os.pathsep.join([str(root / "logs/stock-ml-qa"), str(root / "backend")]),
        "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1"}
    child = spawn_bounded_process([sys.executable, "-u", "-c", code], cwd=root, env=environment)
    async def heartbeat():
        return True
    async def reject_snapshot(value):
        raise AssertionError("计算QA不能发布数据库快照")
    try:
        result = await supervise_process(child, heartbeat=heartbeat, snapshot=reject_snapshot, timeout_seconds=30, max_message_bytes=4096)
        assert result["folds"] == 2 and result["candidates"] == 600
        stats = child.stats()
        assert child.poll() is not None and stats["active_processes"] == 0
        assert stats["memory_limit_bytes"] == 512 * 1024 * 1024 and stats["process_limit"] == 4
        assert 0 < stats["peak_committed_bytes"] <= stats["memory_limit_bytes"]
        saved = json.loads((tmp_path / "qa-report.json").read_text(encoding="utf-8"))
        assert saved["experiment"]["evaluation"] is None
        assert saved["evaluation"]["holdout_registry_verified"] is False
        print("walk_forward_native_qa_stats=" + json.dumps(stats))
    finally:
        child.close()
