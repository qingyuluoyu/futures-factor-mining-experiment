from uuid import uuid4

import numpy as np
import pytest

from app.schemas.stock_research_v2 import Scope, DatasetRef
from app.services.stock_research_jobs import JobError
from tests.test_stock_ml_dataset import dataset, split_spec


def arguments():
    from app.schemas.stock_ml import TrainingParameters
    from app.services.stock_research_v2.ml_dataset import split_samples
    samples, data, _, _ = dataset()
    return split_samples(samples, split_spec(data)), {"scope": Scope(owner_id=uuid4(), trading_mode="virtual"),
        "dataset": DatasetRef(dataset_id=uuid4(), data_version="a"*64, qualification="observation_only"),
        "feature_versions": {"quality": "b"*64, "value": "c"*64},
        "parameters": TrainingParameters(n_estimators=40, max_depth=4, num_leaves=15, min_child_samples=5), "qa_only": True}


def test_real_cpu_ranker_is_deterministic_serializable_and_predicts_later_candidates():
    from app.services.stock_research_v2.ml_train import fit_ranker, predict_ranker, restore_ranker
    fold, options = arguments()
    first = fit_ranker(fold, **options); second = fit_ranker(fold, **options)
    assert first.manifest.model_sha256 == second.manifest.model_sha256
    assert first.manifest.runtime["lightgbm"] == "4.7.0" and first.manifest.parameters.threads == 2
    assert sum(first.manifest.feature_gain.values()) > 0
    recovered = restore_ranker(first.to_payload(), scope=options["scope"], dataset=options["dataset"])
    predictions = predict_ranker(recovered, fold.test_candidates, scope=options["scope"], dataset=options["dataset"], feature_versions=options["feature_versions"])
    assert len(predictions) == len(fold.test_candidates) and np.isfinite(predictions.score).all()
    one_day = predictions.loc[predictions.decision_date.eq(predictions.decision_date.min())].sort_values("instrument_id")
    assert one_day.iloc[-1].score > one_day.iloc[0].score
    bad = first.to_payload(); bad["model_text"] += "tampered"
    with pytest.raises(JobError): restore_ranker(bad, scope=options["scope"], dataset=options["dataset"])
    with pytest.raises(JobError): restore_ranker(first.to_payload(), scope=Scope(owner_id=uuid4(), trading_mode="virtual"), dataset=options["dataset"])


def test_test_labels_do_not_change_training_or_predictions_and_earlier_prediction_is_rejected():
    from app.services.stock_research_v2.ml_train import fit_ranker, predict_ranker
    fold, options = arguments(); model = fit_ranker(fold, **options)
    fold.test_candidates.loc[:, "target_return"] = -1
    again = fit_ranker(fold, **options)
    assert again.manifest.model_sha256 == model.manifest.model_sha256
    with pytest.raises(JobError): predict_ranker(model, fold.train, scope=options["scope"], dataset=options["dataset"], feature_versions=options["feature_versions"])
    changed = {**options["feature_versions"], "quality": "d"*64}
    with pytest.raises(JobError): predict_ranker(model, fold.test_candidates, scope=options["scope"], dataset=options["dataset"], feature_versions=changed)


def test_unqualified_data_requires_explicit_qa_mode_and_parameters_cannot_inject_native_options():
    from app.schemas.stock_ml import TrainingParameters
    from app.services.stock_research_v2.ml_train import fit_ranker
    fold, options = arguments(); options["qa_only"] = False
    with pytest.raises(JobError): fit_ranker(fold, **options)
    for patch in [{"objective": "regression"}, {"threads": 100}, {"n_estimators": 501}, {"max_depth": 2, "num_leaves": 63}, {"seed": True}]:
        with pytest.raises(ValueError): TrainingParameters(**patch)


def test_mutated_fold_cannot_truncate_fractional_labels_or_ignore_invalid_features():
    from app.services.stock_research_v2.ml_train import fit_ranker
    for patch in ["fractional_label", "infinite_feature", "bad_window", "too_small_query"]:
        fold, options = arguments()
        if patch == "fractional_label":
            fold.train["relevance"] = fold.train.relevance.astype(float)
            fold.train.loc[fold.train.index[0], "relevance"] = 0.5
        elif patch == "infinite_feature":
            fold.train.loc[fold.train.index[0], "quality"] = np.inf
        elif patch == "bad_window":
            fold.train.loc[fold.train.index[0], "label_start"] = fold.train.decision_date.iloc[0]
        else:
            fold.train.drop(fold.train.index[1:30], inplace=True)
        with pytest.raises(JobError): fit_ranker(fold, **options)


def test_training_cancel_check_stops_native_iterations_without_publishing_a_model():
    from app.services.stock_research_v2.ml_train import fit_ranker
    fold, options = arguments()
    with pytest.raises(JobError) as stopped:
        fit_ranker(fold, **options, cancel_check=lambda: True)
    assert stopped.value.code == "ml_cancelled"


def test_non_alphabetic_feature_order_survives_canonical_store_and_map_reordering(tmp_path):
    from dataclasses import replace
    from app.services.stock_research_v2.artifacts import ArtifactStore
    from app.services.stock_research_v2.ml_train import fit_ranker, predict_ranker, restore_ranker
    fold, options = arguments()
    fold = replace(fold, feature_names=("value", "quality"))
    options["feature_versions"] = {"value": "c"*64, "quality": "b"*64}
    model = fit_ranker(fold, **options)
    identity = {key: options[key] for key in ["scope", "dataset"]}
    first = predict_ranker(model, fold.test_candidates, **identity, feature_versions=options["feature_versions"])
    store = ArtifactStore(tmp_path)
    saved = store.put_json(model.to_payload())
    payload = store.read_json(saved.sha256)
    assert payload["manifest"]["feature_order"] == ["value", "quality"]
    assert list(payload["manifest"]["feature_versions"]) == ["quality", "value"]
    restored = restore_ranker(payload, **identity)
    second = predict_ranker(restored, fold.test_candidates, **identity, feature_versions=restored.manifest.feature_versions)
    np.testing.assert_array_equal(first.score.to_numpy(), second.score.to_numpy())
    # Feature matrices use explicit fold/manifest order; map insertion order is irrelevant.
    again = fit_ranker(fold, **{**options, "feature_versions": restored.manifest.feature_versions})
    assert again.manifest.model_sha256 == model.manifest.model_sha256
    for invalid in [{"quality": "b"*64}, {"quality": "b"*64, "value": "d"*64},
            {"quality": "b"*64, "value": "c"*64, "unexpected": "e"*64}]:
        with pytest.raises(JobError): predict_ranker(restored, fold.test_candidates, **identity, feature_versions=invalid)
