import numpy as np
import pandas as pd
import pytest

from tests.test_stock_factor_values_v2 import frames


def dataset():
    from app.services.stock_research_v2.analysis import forward_returns
    from app.services.stock_research_v2.ml_dataset import build_ml_dataset
    data = frames()
    dates = data["close"].index
    windows = {date: (dates[index+1], dates[index+4]) if index+4 < len(dates) else None for index, date in enumerate(dates)}
    returns = forward_returns(data["close"], 3)
    return build_ml_dataset({"quality": data["roe"], "value": -data["pb"]}, returns, windows), data, returns, windows


def split_spec(data):
    from app.schemas.stock_ml import TimeSplit
    dates = data["close"].index
    return TimeSplit(train_start=dates[0].date(), train_end=dates[39].date(), validation_start=dates[40].date(), validation_end=dates[59].date(), test_start=dates[60].date(), test_end=dates[-1].date())


def test_time_split_purges_overlapping_labels_and_never_randomizes_dates():
    from app.services.stock_research_v2.ml_dataset import split_samples
    samples, data, _, _ = dataset()
    fold = split_samples(samples, split_spec(data))
    assert fold.train.label_end.max().date() < fold.spec.validation_start
    assert fold.validation.label_end.max().date() < fold.spec.test_start
    assert fold.purge_counts["train"] == 4 * 30 and fold.purge_counts["validation"] == 4 * 30
    assert len(fold.test_candidates) == 30 * 30
    assert list(fold.train.instrument_id.iloc[:30]) == sorted(fold.train.instrument_id.iloc[:30])


def test_equal_future_returns_keep_equal_relevance_and_missing_labels_do_not_delete_candidates():
    from app.services.stock_research_v2.ml_dataset import build_ml_dataset
    _, data, target, windows = dataset()
    target.iloc[0, :6] = 0.01
    target.iloc[65, -1] = np.nan
    sample = build_ml_dataset({"quality": data["roe"]}, target, windows)
    first = sample.candidates.loc[sample.candidates.decision_date.eq(target.index[0])]
    assert first.iloc[:6].relevance.nunique() == 1
    missing = sample.candidates.loc[sample.candidates.decision_date.eq(target.index[65]) & sample.candidates.instrument_id.eq(target.columns[-1])]
    assert len(missing) == 1 and missing.target_return.isna().all()
    assert len(sample.candidates) == target.size
    with pytest.raises(ValueError): build_ml_dataset({"target_return": data["roe"]}, target, windows)


def test_flat_labels_are_not_a_training_query_and_inconsistent_windows_fail():
    from app.services.stock_research_v2.ml_dataset import build_ml_dataset, split_samples
    _, data, target, windows = dataset()
    target.iloc[:] = 0.01
    for date, window in windows.items():
        if window is None: target.loc[date] = np.nan
    samples = build_ml_dataset({"quality": data["roe"]}, target, windows)
    with pytest.raises(ValueError): split_samples(samples, split_spec(data))
    bad = dict(windows); bad[target.index[0]] = (target.index[0], target.index[1])
    with pytest.raises(ValueError): build_ml_dataset({"quality": data["roe"]}, target, bad)
