import numpy as np
import pandas as pd
import pytest


def inputs():
    dates = pd.to_datetime(["2020-01-02"]*5 + ["2020-01-03"]*5)
    truth = pd.DataFrame({"decision_date": dates, "instrument_id": [f"00000{i}.SZ" for i in range(5)]*2,
        "target_return": [0., .01, .02, .03, .04]*2})
    prediction = truth[["decision_date", "instrument_id"]].copy()
    prediction["score"] = list(range(5))*2
    return prediction, truth


def test_rankic_ndcg_and_group_returns_are_per_date_without_portfolio_claims():
    from app.services.stock_research_v2.ml_evaluate import evaluate_prediction
    prediction, truth = inputs()
    report = evaluate_prediction(prediction, truth, groups=5, ndcg_at=3)
    assert report["mean_rank_ic"] == pytest.approx(1.)
    assert report["mean_ndcg"] == pytest.approx(1.)
    assert report["counts"]["evaluated"] == 10
    assert report["daily"][0]["groups"][-1]["mean_label_return"] == .04
    assert report["status"] == "exploratory_not_certified"
    assert report["holdout_registry_verified"] is False
    assert report["portfolio_backtest"]["available"] is False


def test_missing_predictions_labels_and_empty_date_are_not_silently_dropped():
    from app.services.stock_research_v2.ml_evaluate import evaluate_prediction
    prediction, truth = inputs()
    prediction = prediction.iloc[:4]
    truth.loc[4, "target_return"] = np.nan
    truth.loc[3, "target_return"] = np.nan
    report = evaluate_prediction(prediction, truth, groups=2)
    assert report["counts"] == {"candidates": 10, "evaluated": 3, "missing_prediction_only": 5,
        "missing_label_only": 1, "missing_prediction_and_label": 1}
    assert len(report["daily"]) == 2
    assert report["daily"][1]["rank_ic"] is None
    assert any(group["mean_label_return"] is None for group in report["daily"][0]["groups"])


def test_tied_scores_do_not_gain_arbitrary_code_order_and_invalid_outputs_fail():
    from app.services.stock_research_v2.ml_evaluate import evaluate_prediction
    prediction, truth = inputs()
    prediction["score"] = 1.
    first = evaluate_prediction(prediction, truth)
    assert first["daily"][0]["rank_ic"] is None
    assert first["daily"][0]["groups_reason"] == "insufficient_unique_scores"
    assert first == evaluate_prediction(prediction.iloc[::-1], truth)
    for bad in [pd.concat([prediction, prediction.iloc[:1]]), prediction.assign(score=np.inf), prediction.assign(instrument_id="999999.SZ")]:
        with pytest.raises(ValueError): evaluate_prediction(bad, truth)


def test_whole_prediction_or_label_window_missing_is_reported_with_no_metric():
    from app.services.stock_research_v2.ml_evaluate import evaluate_prediction
    prediction, truth = inputs()
    prediction["score"] = None
    report = evaluate_prediction(prediction, truth)
    assert report["counts"]["missing_prediction_only"] == 10
    assert report["mean_rank_ic"] is None and report["mean_ndcg"] is None
    truth["target_return"] = None
    assert evaluate_prediction(prediction, truth)["counts"]["missing_prediction_and_label"] == 10
