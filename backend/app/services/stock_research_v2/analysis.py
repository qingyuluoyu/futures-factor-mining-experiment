"""Versioned descriptive factor analysis; theoretical spread is not tradable PnL."""
import math

import numpy as np
import pandas as pd

from app.services.stock_factors.engine import MarketData, forward_return
from app.services.stock_research_v2.factor_values import validate_frame

METHOD_VERSION = "stock-factor-analysis-2026-10-25.v1"


def forward_returns(close, horizon):
    if type(horizon) is not int or not 1 <= horizon <= 120:
        raise ValueError("未来收益窗口须为1至120个明确交易日")
    close = validate_frame(close)
    if ((close <= 0) & close.notna()).any().any():
        raise ValueError("收益输入价格非正")
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        return forward_return(MarketData(close, {}, {}, {}, {}), horizon).replace([np.inf, -np.inf], np.nan)


def correlations(values, target):
    pairs = pd.concat([values, target], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
    if len(pairs) < 5 or (pairs.nunique() < 2).any():
        return None, None, len(pairs)
    scaled = pairs / pairs.abs().max()
    ic, rank_ic = float(scaled.corr().iloc[0, 1]), float(pairs.rank(method="average").corr().iloc[0, 1])
    return ic if math.isfinite(ic) else None, rank_ic if math.isfinite(rank_ic) else None, len(pairs)


def buckets(values, groups):
    cross = values.dropna()
    if len(cross) < groups * 5 or cross.nunique() < groups:
        return None
    result = pd.qcut(cross, groups, labels=False, duplicates="drop")
    return result if result.nunique() == groups else None


def analyze_values(values, close, *, direction="high", horizon=20, groups=5, decay_lags=(1, 5, 10, 20, 40, 60)):
    close = validate_frame(close)
    values = validate_frame(values, close)
    if values.size * 3 > 2_000_000 or direction not in {"high", "low"} or type(groups) is not int or not 2 <= groups <= 10 or not 1 <= len(decay_lags) <= 12:
        raise ValueError("因子方向、分组或计算预算无效")
    signed = values * (1 if direction == "high" else -1)
    target = forward_returns(close, horizon)
    rows = []
    for date in values.index:
        ic, rank_ic, count = correlations(signed.loc[date], target.loc[date])
        rows.append({"date": str(date.date()), "ic": ic, "rank_ic": rank_ic, "pairs": count})
    summary = {}
    for name in ["ic", "rank_ic"]:
        observations = [row[name] for row in rows if row[name] is not None]
        mean = float(np.mean(observations)) if observations else None
        std = float(np.std(observations, ddof=1)) if len(observations) > 1 else None
        summary.update({name + "_mean": mean, name + "_std": std, name + "_ir": mean / std * math.sqrt(244) if std is not None and std > 1e-12 else None})
    decay = []
    for lag in decay_lags:
        future = forward_returns(close, lag)
        observed = [correlations(signed.loc[date], future.loc[date])[1] for date in signed.index]
        observed = [value for value in observed if value is not None]
        decay.append({"lag": lag, "rank_ic": float(np.mean(observed)) if observed else None, "periods": len(observed)})
    observations, first_signal = [], None
    for position, date in enumerate(signed.index):
        selected = buckets(signed.loc[date], groups)
        if selected is None:
            continue
        if first_signal is None:
            first_signal = position
        if (position - first_signal) % horizon or position + horizon + 1 >= len(signed.index):
            continue
        for group in range(groups):
            members = list(selected[selected == group].index)
            realized = target.loc[date, members]
            complete = realized.notna().all()
            observations.append({"date": str(date.date()), "group": group, "members": members, "return": float(realized.mean()) if complete else None,
                "reason": None if complete else "incomplete_selected_group_returns"})
    averages = []
    for group in range(groups):
        valid = [row["return"] for row in observations if row["group"] == group and row["return"] is not None]
        if valid:
            averages.append({"group": group, "avg_return": float(np.mean(valid)), "periods": len(valid)})
    daily = forward_returns(close, 1)
    curve, nav, broken = [], 1., False
    for position in range(len(signed.index) - 2):
        selected = buckets(signed.iloc[position], groups)
        if selected is None:
            if not curve:
                continue
            broken = True
            curve.append({"date": str(signed.index[position + 2].date()), "nav": None, "reason": "missing_factor_cross_section_or_prior_gap"})
            continue
        top, bottom = selected[selected == groups - 1].index, selected[selected == 0].index
        realized = daily.iloc[position]
        if realized.loc[top.union(bottom)].isna().any():
            broken = True
        if not broken:
            nav *= 1 + float(realized.loc[top].mean() - realized.loc[bottom].mean())
            if not math.isfinite(nav) or nav <= 0:
                raise ValueError("理论多空净值非正或非有限")
        curve.append({"date": str(signed.index[position + 2].date()), "nav": None if broken else nav, "reason": "missing_selected_return_or_prior_gap" if broken else None})
    return {"method_version": METHOD_VERSION, "validation_status": "calculation_only_unverified_inputs", "ic": summary, "ic_series": rows,
        "decay": decay, "group_observations": observations, "quantile_returns": averages, "long_short_nav": curve,
        "method": {"periods_per_year": 244, "std_ddof": 1, "forward_return": "next_session_close_to_horizon_later_close", "group_observations": "nonoverlapping_horizon_from_first_factor_eligible_date",
            "long_short": "daily_1_long_1_short_zero_cost_theoretical", "ir": "descriptive_sqrt_244_not_overlap_adjusted"},
        "limitations": ["输入交易日轴、PIT、复权及历史股票池须由数据资格层验证，本函数不授予资格。", "多空为零费用理论序列，未验证借券、整手、停牌和成交；缺收益后净值不跨缺口续接。"]}
