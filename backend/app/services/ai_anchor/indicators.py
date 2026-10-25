"""AI 看盘主播技术线计算 —— 覆盖全部现有技术线

复用 ai/tools/calc.py 纯函数，
输出结构化摘要（最新值/近几拍/信号）供主播 prompt 使用。
"""

from __future__ import annotations

from typing import Any

from app.services.ai.tools.calc import (
    calc_boll,
    calc_ema,
    calc_kdj,
    calc_macd,
    calc_rsi,
    calc_sma,
    extract_series_from_bars,
    last_valid_points,
)
SUPPORTED_ANCHOR_INDICATORS = ("MA", "EMA", "BOLL", "MACD", "RSI", "KDJ")

# 每条技术线的参数 schema：供 GET /api/ai-anchor/indicators 下发前端渲染参数表单
INDICATOR_PARAM_SCHEMA: dict[str, list[dict[str, Any]]] = {
    "MA": [
        {"key": "period", "label": "周期", "default": 20, "min": 2, "max": 200, "step": 1},
    ],
    "EMA": [
        {"key": "period", "label": "周期", "default": 20, "min": 2, "max": 200, "step": 1},
    ],
    "BOLL": [
        {"key": "period", "label": "周期", "default": 20, "min": 2, "max": 200, "step": 1},
        {"key": "std", "label": "标准差倍数", "default": 2.0, "min": 0.5, "max": 5.0, "step": 0.1},
    ],
    "MACD": [
        {"key": "fast", "label": "快线", "default": 12, "min": 2, "max": 200, "step": 1},
        {"key": "slow", "label": "慢线", "default": 26, "min": 2, "max": 200, "step": 1},
        {"key": "signal", "label": "信号线", "default": 9, "min": 2, "max": 200, "step": 1},
    ],
    "RSI": [
        {"key": "period", "label": "周期", "default": 14, "min": 2, "max": 200, "step": 1},
        {"key": "overbought", "label": "超买阈值", "default": 70, "min": 50, "max": 100, "step": 1},
        {"key": "oversold", "label": "超卖阈值", "default": 30, "min": 0, "max": 50, "step": 1},
    ],
    "KDJ": [
        {"key": "n", "label": "RSV周期", "default": 9, "min": 2, "max": 200, "step": 1},
        {"key": "k", "label": "K平滑", "default": 3, "min": 1, "max": 50, "step": 1},
        {"key": "d", "label": "D平滑", "default": 3, "min": 1, "max": 50, "step": 1},
    ],
}

# 近几拍点数（喂给模型的 each 指标 recent 序列长度）
_RECENT_POINTS = 5


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    """安全转 int 并夹到范围内。"""
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, result))


def _clamp_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    """安全转 float 并夹到范围内。"""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, result))


def normalize_params(name: str, params: dict[str, Any] | None) -> dict[str, Any]:
    """按指标 schema 归一化参数（缺省补默认值，越界夹回）。"""
    raw = params if isinstance(params, dict) else {}
    used: dict[str, Any] = {}
    for spec in INDICATOR_PARAM_SCHEMA.get(name, []):
        if spec["step"] == 1:
            used[spec["key"]] = _clamp_int(
                raw.get(spec["key"]), int(spec["default"]), int(spec["min"]), int(spec["max"])
            )
        else:
            used[spec["key"]] = _clamp_float(
                raw.get(spec["key"]), float(spec["default"]), float(spec["min"]), float(spec["max"])
            )
    return used


def _detect_macd_cross(points: list[dict[str, Any]]) -> str | None:
    """检测最近 DIF/DEA 金叉死叉。"""
    if len(points) < 2:
        return None
    prev = points[-2]
    curr = points[-1]
    prev_dif = prev.get("dif")
    prev_dea = prev.get("dea")
    curr_dif = curr.get("dif")
    curr_dea = curr.get("dea")
    if None in (prev_dif, prev_dea, curr_dif, curr_dea):
        return None
    if prev_dif <= prev_dea and curr_dif > curr_dea:
        return "golden_cross"
    if prev_dif >= prev_dea and curr_dif < curr_dea:
        return "death_cross"
    return None


def compute_indicator_summary(
    name: str,
    params: dict[str, Any] | None,
    bars: list[dict[str, Any]],
) -> dict[str, Any]:
    """在给定 K 线上计算单条技术线摘要（纯函数，不拉数据）。"""
    indicator = name.strip().upper()
    used = normalize_params(indicator, params)
    times, _opens, highs, lows, closes, _volumes, _ois = extract_series_from_bars(bars)
    if len(closes) < 2:
        return {"name": indicator, "params": used, "latest": None, "recent_points": [], "signal": None}

    if indicator == "MA":
        series = calc_sma(closes, int(used["period"]))
    elif indicator == "EMA":
        series = calc_ema(closes, int(used["period"]))
    elif indicator == "BOLL":
        series = calc_boll(closes, int(used["period"]), float(used["std"]))
    elif indicator == "MACD":
        series = calc_macd(closes, int(used["fast"]), int(used["slow"]), int(used["signal"]))
    elif indicator == "RSI":
        series = calc_rsi(closes, int(used["period"]))
    elif indicator == "KDJ":
        series = calc_kdj(highs, lows, closes, int(used["n"]), int(used["k"]), int(used["d"]))
    else:
        return {"name": indicator, "params": used, "latest": None, "recent_points": [], "signal": None}

    recent_points = last_valid_points(times, series, _RECENT_POINTS)
    latest = recent_points[-1] if recent_points else None
    signal: str | None = None
    if indicator == "MACD":
        signal = _detect_macd_cross(recent_points)
    elif indicator == "RSI" and latest is not None:
        rsi_value = latest.get("value")
        if isinstance(rsi_value, (int, float)):
            if rsi_value >= float(used["overbought"]):
                signal = "overbought"
            elif rsi_value <= float(used["oversold"]):
                signal = "oversold"
    elif indicator == "BOLL" and latest is not None and closes:
        last_close = closes[-1]
        upper = latest.get("upper")
        lower = latest.get("lower")
        if isinstance(upper, (int, float)) and last_close >= upper:
            signal = "touch_upper"
        elif isinstance(lower, (int, float)) and last_close <= lower:
            signal = "touch_lower"
    elif indicator == "KDJ" and latest is not None:
        j_value = latest.get("j")
        if isinstance(j_value, (int, float)):
            if j_value >= 100:
                signal = "overbought"
            elif j_value <= 0:
                signal = "oversold"
    return {
        "name": indicator,
        "params": used,
        "latest": latest,
        "recent_points": recent_points,
        "signal": signal,
    }


def summarize_indicators(
    items: list[dict[str, Any]],
    bars: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """按用户选择的技术线组合，在给定 K 线上逐条计算摘要。"""
    summaries: list[dict[str, Any]] = []
    for item in items:
        name = str(item.get("name") or "").strip().upper()
        if name not in SUPPORTED_ANCHOR_INDICATORS:
            continue
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        summaries.append(compute_indicator_summary(name, params, bars))
    return summaries
