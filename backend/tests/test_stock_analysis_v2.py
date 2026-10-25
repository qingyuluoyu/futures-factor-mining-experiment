import numpy as np
import pytest

from tests.test_stock_factor_values_v2 import frames


def test_ic_decay_groups_and_theoretical_nav_use_one_factor_value_contract():
    from app.services.stock_research_v2.analysis import analyze_values
    data = frames()
    result = analyze_values(data["roe"], data["close"], horizon=5, groups=5, decay_lags=[1, 5, 20])
    assert result["ic"]["rank_ic_mean"] == pytest.approx(1)
    assert all(point["rank_ic"] == pytest.approx(1) for point in result["decay"])
    groups = result["quantile_returns"]
    assert len(groups) == 5 and all(groups[i]["avg_return"] < groups[i+1]["avg_return"] for i in range(4))
    assert result["long_short_nav"][-1]["nav"] > 1
    assert result["method"]["long_short"] == "daily_1_long_1_short_zero_cost_theoretical"
    assert result["ic"]["rank_ic_ir"] is None


def test_future_missing_return_does_not_reselect_group_members_or_make_a_fake_zero():
    from app.services.stock_research_v2.analysis import analyze_values
    data = frames(); missing = data["close"].columns[-1]
    data["close"].iloc[6, -1] = np.nan
    result = analyze_values(data["roe"], data["close"], horizon=5, groups=5, decay_lags=[1])
    affected = next(row for row in result["group_observations"] if row["date"] == str(data["close"].index[0].date()) and row["group"] == 4)
    assert missing in affected["members"] and affected["return"] is None
    assert affected["reason"] == "incomplete_selected_group_returns"
    curve = result["long_short_nav"]
    first_gap = next(index for index, row in enumerate(curve) if row["nav"] is None)
    assert all(row["nav"] is None for row in curve[first_gap:])


def test_flat_factors_and_negative_direction_are_explicit_not_fabricated_ic():
    from app.services.stock_research_v2.analysis import analyze_values
    data = frames()
    flat = data["roe"] * 0 + 3
    result = analyze_values(flat, data["close"], horizon=5, groups=5, decay_lags=[1])
    assert result["ic"]["rank_ic_mean"] is None and result["quantile_returns"] == []
    result = analyze_values(data["roe"], data["close"], direction="low", horizon=5, groups=5, decay_lags=[1])
    assert result["ic"]["rank_ic_mean"] == pytest.approx(-1)


def test_missing_factor_cross_section_breaks_started_nav_instead_of_skipping_a_session():
    from app.services.stock_research_v2.analysis import analyze_values
    data = frames()
    data["roe"].iloc[8] = np.nan
    result = analyze_values(data["roe"], data["close"], horizon=5, groups=5, decay_lags=[1])
    gap = str(data["close"].index[10].date())
    row = next(row for row in result["long_short_nav"] if row["date"] == gap)
    assert row["nav"] is None
    assert all(row["nav"] is None for row in result["long_short_nav"] if row["date"] >= gap)


def test_nonfinite_returns_are_missing_not_json_nan_or_infinite_ic():
    from app.services.stock_research_v2.analysis import correlations, forward_returns
    data = frames()
    data["close"].iloc[1] = 1e-308
    data["close"].iloc[2] = 1e308
    with np.errstate(over="ignore"):
        returns = forward_returns(data["close"], 1)
    assert not np.isinf(returns.to_numpy()).any()
    ic, rank_ic, count = correlations(data["roe"].iloc[0], returns.iloc[0])
    assert ic is None and rank_ic is None and count == 0
