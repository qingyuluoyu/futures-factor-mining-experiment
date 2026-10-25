import numpy as np
import pandas as pd
import pytest

from app.services.stock_research_v2.expressions import ExpressionError, compile_expression, evaluate_expression


@pytest.fixture
def panels():
    return {"close": pd.DataFrame({"000001.SZ": [10., 12., 15., 18.], "600001.SH": [20., 18., 16., 14.]}, index=pd.date_range("2025-01-01", periods=4))}


@pytest.mark.parametrize("expression", ['__import__("os")', "close.__class__", "close[0]", "lag(close,-1)", "rolling_mean(close,1000000)", "close ** 2", "close > 0", "log(close,2)", "unknown + 1", "True * close", "1e309 * close", "rank(close,1)", "clip(close,2,1)", "9" * 1000 + " * close"])
def test_untrusted_expression_never_executes(expression):
    with pytest.raises(ExpressionError):
        compile_expression(expression, allowed_fields={"close"})


def test_lag_rolling_arithmetic_and_version_are_deterministic(panels):
    compiled = compile_expression("rolling_mean(close, 2) / lag(close, 1) - 1", allowed_fields={"close"})
    result = evaluate_expression(compiled, panels)
    expected = panels["close"].rolling(2).mean() / panels["close"].shift(1) - 1
    pd.testing.assert_frame_equal(result, expected)
    assert compiled.lookback == 1 and compiled.fields == ("close",)
    assert len(compiled.version) == 64
    assert compiled == compile_expression(" rolling_mean(close,2)/lag(close,1)-1 ", allowed_fields={"close"})


def test_future_price_suffix_never_changes_previous_values(panels):
    compiled = compile_expression("rolling_std(lag(close,1),2)", allowed_fields={"close"})
    expected = evaluate_expression(compiled, panels)
    assert compiled.lookback == 2
    changed = panels["close"].copy(); changed.iloc[-1] = 9999
    pd.testing.assert_frame_equal(expected, evaluate_expression(compiled, {"close": changed}))


def test_invalid_log_division_and_nonfinite_values_remain_missing(panels):
    compiled = compile_expression("log(close) / (close-close)", allowed_fields={"close"})
    assert evaluate_expression(compiled, panels).isna().all().all()
    compiled = compile_expression("log(close)", allowed_fields={"close"})
    modified = panels["close"].copy(); modified.iloc[0] = [-1, np.inf]
    assert evaluate_expression(compiled, {"close": modified}).iloc[0].isna().all()


def test_rank_zscore_clip_and_negative_values(panels):
    for expression, expected in [("rank(close)", panels["close"].rank(axis=1, pct=True)), ("clip(close,-1,15)", panels["close"].clip(-1, 15)), ("abs(-close)", panels["close"])]:
        pd.testing.assert_frame_equal(evaluate_expression(compile_expression(expression, allowed_fields={"close"}), panels), expected)
    result = evaluate_expression(compile_expression("zscore(close)", allowed_fields={"close"}), panels)
    assert np.allclose(result.mean(axis=1), 0)


def test_missing_fields_alignment_and_resource_limits_fail(panels):
    compiled = compile_expression("close + pb", allowed_fields={"close", "pb"})
    with pytest.raises(ExpressionError): evaluate_expression(compiled, panels)
    with pytest.raises(ExpressionError): evaluate_expression(compiled, {**panels, "pb": panels["close"].iloc[1:]})
    with pytest.raises(ExpressionError): evaluate_expression(compile_expression("close", allowed_fields={"close"}), panels, max_cells=1)
    with pytest.raises(ExpressionError): compile_expression("+".join(["close"] * 80), allowed_fields={"close"})
    with pytest.raises(ExpressionError): compile_expression("1+2", allowed_fields={"close"})
    with pytest.raises(ExpressionError): evaluate_expression(compile_expression("close", allowed_fields={"close"}), {"close": panels["close"] > 0})
