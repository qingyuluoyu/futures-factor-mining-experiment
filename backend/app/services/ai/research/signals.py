"""技术面默认包 —— 纯函数打分 long / short / range + 可操作点位"""

from __future__ import annotations

from typing import Any

from app.services.ai.research.levels import (
    compute_long_levels,
    compute_short_levels,
    empty_levels,
)
from app.services.ai.research.types import Bias, ProductSignal
from app.services.ai.tools.calc import calc_macd, calc_rsi, calc_sma


def _last_valid(series: list[float | None]) -> float | None:
    """取序列最后一个非 None 值。"""
    for value in reversed(series):
        if value is not None:
            return float(value)
    return None


def _closes_from_bars(bars: list[dict[str, Any]]) -> list[float]:
    """从 K 线提取收盘价。"""
    closes: list[float] = []
    for bar in bars:
        close = bar.get("close")
        if close is None:
            continue
        try:
            closes.append(float(close))
        except (TypeError, ValueError):
            continue
    return closes


def _recent_high_low(
    bars: list[dict[str, Any]],
    lookback: int,
) -> tuple[float | None, float | None]:
    """近 N 根高低点。"""
    window = bars[-lookback:] if lookback > 0 else bars
    highs: list[float] = []
    lows: list[float] = []
    for bar in window:
        try:
            if bar.get("high") is not None:
                highs.append(float(bar["high"]))
            if bar.get("low") is not None:
                lows.append(float(bar["low"]))
        except (TypeError, ValueError):
            continue
    high = max(highs) if highs else None
    low = min(lows) if lows else None
    return high, low


def _oi_change(bars: list[dict[str, Any]]) -> float | None:
    """首尾持仓量变化。"""
    oi_values: list[float] = []
    for bar in bars:
        oi = bar.get("open_interest")
        if oi is None:
            oi = bar.get("openInterest")
        if oi is None:
            continue
        try:
            oi_values.append(float(oi))
        except (TypeError, ValueError):
            continue
    if len(oi_values) < 2:
        return None
    return oi_values[-1] - oi_values[0]


def _direction_meta(bias: Bias) -> tuple[str, str]:
    """bias → (direction_zh, action)。"""
    if bias == "long":
        return "做多", "买入开多"
    if bias == "short":
        return "做空", "卖出开空"
    return "震荡", "观望"


def _base_signal(
    product_code: str,
    symbol: str,
    product_name: str,
    bias: Bias,
    confidence: float,
    last_price: float | None,
    change_pct: float | None,
    reasons: list[str],
    as_of: str | None,
    levels: dict[str, float | None],
) -> ProductSignal:
    """组装完整 ProductSignal。"""
    direction_zh, action = _direction_meta(bias)
    return {
        "symbol": symbol,
        "product_code": product_code,
        "product_name": product_name,
        "bias": bias,
        "direction_zh": direction_zh,
        "action": action,
        "confidence": round(confidence, 3),
        "last_price": last_price,
        "change_pct": change_pct,
        "entry": levels.get("entry"),
        "support": levels.get("support"),
        "resistance": levels.get("resistance"),
        "stop_loss": levels.get("stop_loss"),
        "take_profit": levels.get("take_profit"),
        "stop": levels.get("stop_loss"),
        "target": levels.get("take_profit"),
        "risk_reward": levels.get("risk_reward"),
        "reasons": reasons,
        "as_of": as_of,
    }


def score_product(
    product_code: str,
    symbol: str,
    product_name: str,
    bars: list[dict[str, Any]],
    quote: dict[str, Any] | None,
) -> ProductSignal:
    """根据 K 线 + 报价计算方向与入场/支撑/压力/止损/止盈。"""
    as_of: str | None = None
    if bars:
        as_of = str(bars[-1].get("time") or "") or None

    last_price: float | None = None
    change_pct: float | None = None
    if quote:
        try:
            if quote.get("last_price") is not None:
                last_price = float(quote["last_price"])
            elif quote.get("lastPrice") is not None:
                last_price = float(quote["lastPrice"])
        except (TypeError, ValueError):
            last_price = None
        try:
            # tool_get_quote 返回的统一契约字段是 change_pct（旧键名恒取不到）
            raw_pct = quote.get("change_pct")
            if raw_pct is None:
                raw_pct = quote.get("change_percent")
            if raw_pct is not None:
                change_pct = float(raw_pct)
        except (TypeError, ValueError):
            change_pct = None

    closes = _closes_from_bars(bars)
    if last_price is None and closes:
        last_price = closes[-1]

    if len(closes) < 30:
        return _base_signal(
            product_code,
            symbol,
            product_name,
            "range",
            0.0,
            last_price,
            change_pct,
            ["K线数据不足，无法判定趋势"],
            as_of,
            empty_levels(last_price),
        )

    ma5 = _last_valid(calc_sma(closes, 5))
    ma10 = _last_valid(calc_sma(closes, 10))
    ma20 = _last_valid(calc_sma(closes, 20))
    macd_points = calc_macd(closes, 12, 26, 9)
    last_macd = macd_points[-1] if macd_points else {}
    dif = last_macd.get("dif")
    dea = last_macd.get("dea")
    hist = last_macd.get("macd")
    rsi = _last_valid(calc_rsi(closes, 14))
    close = closes[-1]
    high20, low20 = _recent_high_low(bars, 20)
    oi_delta = _oi_change(bars)

    long_score = 0
    short_score = 0
    reasons_long: list[str] = []
    reasons_short: list[str] = []

    if ma20 is not None and close > ma20:
        long_score += 1
        reasons_long.append("收盘在MA20上方")
    if ma20 is not None and close < ma20:
        short_score += 1
        reasons_short.append("收盘在MA20下方")

    if ma5 is not None and ma10 is not None and ma5 > ma10:
        long_score += 1
        reasons_long.append("MA5>MA10")
    if ma5 is not None and ma10 is not None and ma5 < ma10:
        short_score += 1
        reasons_short.append("MA5<MA10")

    macd_bull = (hist is not None and float(hist) >= 0) or (
        dif is not None and dea is not None and float(dif) > float(dea)
    )
    macd_bear = (hist is not None and float(hist) < 0) or (
        dif is not None and dea is not None and float(dif) < float(dea)
    )
    if macd_bull:
        long_score += 1
        reasons_long.append("MACD多头")
    if macd_bear:
        short_score += 1
        reasons_short.append("MACD空头")

    if rsi is not None and rsi > 75:
        reasons_long.append("RSI超买，追多需谨慎")
    if rsi is not None and rsi < 25:
        reasons_short.append("RSI超卖，追空需谨慎")

    if oi_delta is not None and change_pct is not None:
        if oi_delta > 0 and change_pct > 0:
            long_score += 1
            reasons_long.append("放量增仓上涨")
        if oi_delta > 0 and change_pct < 0:
            short_score += 1
            reasons_short.append("增仓下跌")

    reasons: list[str] = []
    sideways = False
    if ma20 is not None and ma20 != 0 and ma5 is not None and ma10 is not None:
        dist_ma20 = abs(close - ma20) / abs(ma20)
        ma_spread = abs(ma5 - ma10) / max(abs(ma10), 1e-9)
        if dist_ma20 < 0.012 and ma_spread < 0.006:
            sideways = True
            reasons.append("均线缠绕且贴近MA20，判定震荡")

    clear_long = (
        not sideways and long_score >= 3 and long_score >= short_score + 1
    )
    clear_short = (
        not sideways and short_score >= 3 and short_score >= long_score + 1
    )

    if clear_long:
        levels = compute_long_levels(last_price, ma10, ma20, high20, low20)
        return _base_signal(
            product_code,
            symbol,
            product_name,
            "long",
            min(1.0, 0.4 + 0.12 * long_score),
            last_price,
            change_pct,
            reasons_long[:5],
            as_of,
            levels,
        )

    if clear_short:
        levels = compute_short_levels(last_price, ma10, ma20, high20, low20)
        return _base_signal(
            product_code,
            symbol,
            product_name,
            "short",
            min(1.0, 0.4 + 0.12 * short_score),
            last_price,
            change_pct,
            reasons_short[:5],
            as_of,
            levels,
        )

    if not reasons:
        reasons = [
            f"多头分{long_score}/空头分{short_score}，趋势不明，按震荡过滤"
        ]
    if reasons_long:
        reasons.extend(reasons_long[:2])
    if reasons_short:
        reasons.extend(reasons_short[:2])

    return _base_signal(
        product_code,
        symbol,
        product_name,
        "range",
        0.0,
        last_price,
        change_pct,
        reasons,
        as_of,
        empty_levels(last_price),
    )
