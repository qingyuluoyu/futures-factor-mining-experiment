import pandas as pd
import pytest

from tests.test_stock_ml_dataset import dataset


def plan(data, **changes):
    return {"sessions": [day.date() for day in data["close"].index], "warmup_sessions": 3,
        "train_sessions": 30, "validation_sessions": 15, "test_sessions": 10,
        "fold_count": 3, "mode": "expanding", **changes}


def test_expanding_folds_use_supplied_sessions_and_purge_labels():
    from app.services.stock_research_v2.ml_split import make_time_folds
    samples, data, _, _ = dataset()
    folds = make_time_folds(samples, plan(data))
    assert len(folds) == 3
    for number, entry in enumerate(folds):
        fold = entry.fold
        assert fold.train.label_end.max().date() < fold.spec.validation_start
        assert fold.validation.label_end.max().date() < fold.spec.test_start
        assert fold.spec.train_start == data["close"].index[3].date()
        assert fold.spec.train_end == data["close"].index[32+number*10].date()
        assert entry.manifest["purged_samples"] == {"train": 120, "validation": 120}
        assert len(entry.manifest["sample_sha256"]["test_candidates"]) == 64
        assert entry.manifest["warmup_dates"] == [str(day.date()) for day in data["close"].index[:3]]
    assert folds[0].fold.spec.test_end < folds[1].fold.spec.test_start


def test_rolling_keeps_train_window_and_does_not_fabricate_calendar_or_shorten():
    from app.services.stock_research_v2.ml_split import make_time_folds
    samples, data, _, _ = dataset()
    folds = make_time_folds(samples, plan(data, mode="rolling"))
    assert folds[2].fold.spec.train_start == data["close"].index[23].date()
    for changes in [{"fold_count": 5}, {"sessions": list(reversed(plan(data)["sessions"]))},
            {"sessions": plan(data)["sessions"][1:]}, {"train_sessions": True}, {"fold_count": 6}]:
        with pytest.raises(ValueError): make_time_folds(samples, plan(data, **changes))


def test_fold_diagnostics_expose_training_missing_features_and_labels():
    from app.services.stock_research_v2.ml_split import make_time_folds
    samples, data, _, _ = dataset()
    rows = samples.candidates
    row = rows.index[rows.decision_date.eq(data["close"].index[5])][0]
    rows.loc[row, "feature_available"] = False
    rows.loc[row, ["quality", "value"]] = float("nan")
    rows.loc[row+1, ["target_return", "relevance"]] = float("nan")
    entry = make_time_folds(samples, plan(data))[0]
    diagnostic = entry.manifest["distributions"]["train"]
    assert diagnostic["missing_all_features"] == 1
    assert diagnostic["missing_labels"] == 1
    assert diagnostic["retained_rows"] == len(entry.fold.train)
    assert sum(diagnostic["exclusion_reasons"].values()) + diagnostic["retained_rows"] == diagnostic["candidate_rows"]
