import math

import numpy as np

METHOD_VERSION = "stock-metrics-2026-10-23.v1"
PERIODS_PER_YEAR = 244


def _values(sequence):
    if not isinstance(sequence, (list, tuple)) or len(sequence) > 20000:
        raise ValueError("指标序列必须为有界数值列表")
    result = []
    for value in sequence:
        if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, float, np.number)):
            raise ValueError("指标不能包含布尔值或非数值")
        try:
            number = float(value)
        except (OverflowError, ValueError, TypeError):
            raise ValueError("指标数值无效") from None
        if not math.isfinite(number):
            raise ValueError("指标序列不能包含非有限值")
        result.append(number)
    return result


def compute_metrics(returns, nav, turnover, risk_free_annual):
    daily, equity, turnover_values = _values(returns), _values(nav), _values(turnover)
    risk_free = _values([risk_free_annual])[0]
    if not daily or len(daily) != len(equity) or risk_free <= -1 or any(value <= -1 for value in daily) or any(value <= 0 for value in equity) or any(value < 0 for value in turnover_values):
        raise ValueError("收益、净值、换手或无风险收益口径无效")
    previous, peak, drawdown = 1.0, 1.0, 0.0
    for change, observed in zip(daily, equity):
        expected = previous * (1 + change)
        if not math.isclose(observed, expected, rel_tol=1e-10, abs_tol=1e-12):
            raise ValueError("净值必须以初始资产1与每期收益一致，不能省略建仓费用")
        peak = max(peak, observed)
        drawdown = max(drawdown, (peak - observed) / peak)
        previous = observed
    count = len(daily)
    daily_rf = math.expm1(math.log1p(risk_free) / PERIODS_PER_YEAR)
    try:
        annualized = math.expm1(math.log(equity[-1]) * PERIODS_PER_YEAR / count)
    except OverflowError:
        raise ValueError("年化结果超出可表示范围") from None
    volatility = float(np.std(daily, ddof=1)) if count > 1 else None
    reasons = {}
    if volatility is None or volatility == 0:
        reasons["sharpe"] = "insufficient_returns" if volatility is None else "zero_volatility"
    if volatility is None:
        reasons["annualized_vol"] = "insufficient_returns"
    if drawdown == 0:
        reasons["calmar"] = "no_drawdown"
    if not turnover_values:
        reasons["average_rebalance_turnover"] = "no_rebalance_observations"
    result = {
        "method_version": METHOD_VERSION, "periods_per_year": PERIODS_PER_YEAR, "std_ddof": 1,
        "periods": count, "risk_free_annual": risk_free, "risk_free_daily": daily_rf,
        "total_return": equity[-1] - 1, "annualized_return": annualized,
        "annualized_vol": volatility * math.sqrt(PERIODS_PER_YEAR) if volatility is not None else None,
        "sharpe": (float(np.mean(daily)) - daily_rf) / volatility * math.sqrt(PERIODS_PER_YEAR) if volatility else None,
        "max_drawdown": drawdown, "calmar": annualized / drawdown if drawdown else None,
        "positive_day_ratio": sum(value > 0 for value in daily) / count,
        "average_rebalance_turnover": float(np.mean(turnover_values)) if turnover_values else None,
        "undefined_reasons": reasons, "return_unit": "fraction", "turnover_unit": "fraction_per_rebalance",
    }
    if any(isinstance(value, float) and not math.isfinite(value) for value in result.values()):
        raise ValueError("指标计算产生非有限结果")
    return result
