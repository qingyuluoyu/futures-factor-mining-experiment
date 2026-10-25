"""技术指标 Agent 工具 —— 基于 K 线计算 MA/EMA/MACD/RSI/BOLL/KDJ/VOLUME

图表指标配置读写见 app.services.ai.indicator_settings。
"""

from __future__ import annotations

from typing import Any

import redis.asyncio as aioredis

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
from app.services.ai.tools.market import tool_get_kline

_DEFAULT_LOOKBACK = 120
_MAX_LOOKBACK = 200
_DEFAULT_RECENT = 5
_MAX_RECENT = 30
_SUPPORTED_INDICATORS = ("MA", "EMA", "MACD", "RSI", "BOLL", "KDJ", "VOLUME")


def _as_int(value: Any, default: int) -> int:
    """安全转 int。"""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    """安全转 float。"""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_params(indicator: str, params: dict[str, Any] | None) -> dict[str, Any]:
    """按指标给出默认参数。"""
    raw = params if isinstance(params, dict) else {}
    if indicator == "MA":
        return {"period": _as_int(raw.get("period"), 20)}
    if indicator == "EMA":
        return {"period": _as_int(raw.get("period"), 20)}
    if indicator == "MACD":
        return {
            "fast": _as_int(raw.get("fast"), 12),
            "slow": _as_int(raw.get("slow"), 26),
            "signal": _as_int(raw.get("signal"), 9),
        }
    if indicator == "RSI":
        return {"period": _as_int(raw.get("period"), 14)}
    if indicator == "BOLL":
        return {
            "period": _as_int(raw.get("period"), 20),
            "std": _as_float(raw.get("std"), 2.0),
        }
    if indicator == "KDJ":
        return {
            "n": _as_int(raw.get("n"), 9),
            "k": _as_int(raw.get("k"), 3),
            "d": _as_int(raw.get("d"), 3),
        }
    if indicator == "VOLUME":
        return {"ma_period": _as_int(raw.get("ma_period"), 5)}
    return {}


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


async def tool_calculate_indicator(
    symbol: str,
    indicator: str,
    params: dict[str, Any] | None,
    period: str,
    lookback: int | None,
    recent: int | None,
    redis_conn: aioredis.Redis | None,
    *,
    trading_mode: str = "live",
) -> dict[str, Any]:
    """拉取 K 线并计算技术指标，返回最近 N 点 + 最新值摘要。"""
    indicator_name = indicator.strip().upper()
    if indicator_name not in _SUPPORTED_INDICATORS:
        return {
            "success": False,
            "error": f"不支持的指标: {indicator}，可选: {', '.join(_SUPPORTED_INDICATORS)}",
        }

    period_clean = (period or "5m").strip()
    lookback_n = lookback if lookback is not None else _DEFAULT_LOOKBACK
    if lookback_n <= 0:
        lookback_n = _DEFAULT_LOOKBACK
    lookback_n = min(lookback_n, _MAX_LOOKBACK)

    recent_n = recent if recent is not None else _DEFAULT_RECENT
    if recent_n <= 0:
        recent_n = _DEFAULT_RECENT
    recent_n = min(recent_n, _MAX_RECENT)

    kline_result = await tool_get_kline(symbol, period_clean, lookback_n, redis_conn, trading_mode=trading_mode)
    if not kline_result.get("success"):
        return kline_result

    bars = kline_result.get("bars") or []
    if trading_mode == "virtual" and indicator_name == "VOLUME" and any(bar.get("volume") is None for bar in bars):
        return {"success": False, "error_code": "missing_market_data", "error": "虚拟盘成交量字段缺失，未按零计算量能"}
    times, _opens, highs, lows, closes, volumes, open_interests = extract_series_from_bars(bars)
    if len(closes) < 2:
        return {"success": False, "error": "K 线数据不足，无法计算指标"}

    used_params = _normalize_params(indicator_name, params)
    signal: str | None = None
    recent_points: list[dict[str, Any]] = []
    latest: dict[str, Any] | None = None

    if indicator_name == "MA":
        period_n = used_params["period"]
        series = calc_sma(closes, period_n)
        recent_points = last_valid_points(times, series, recent_n)
        latest = recent_points[-1] if recent_points else None
    elif indicator_name == "EMA":
        period_n = used_params["period"]
        series = calc_ema(closes, period_n)
        recent_points = last_valid_points(times, series, recent_n)
        latest = recent_points[-1] if recent_points else None
    elif indicator_name == "MACD":
        series = calc_macd(
            closes,
            used_params["fast"],
            used_params["slow"],
            used_params["signal"],
        )
        recent_points = last_valid_points(times, series, recent_n)
        latest = recent_points[-1] if recent_points else None
        signal = _detect_macd_cross(recent_points)
    elif indicator_name == "RSI":
        series = calc_rsi(closes, used_params["period"])
        recent_points = last_valid_points(times, series, recent_n)
        latest = recent_points[-1] if recent_points else None
        if latest and isinstance(latest.get("value"), (int, float)):
            rsi_value = float(latest["value"])
            if rsi_value >= 70:
                signal = "overbought"
            elif rsi_value <= 30:
                signal = "oversold"
    elif indicator_name == "BOLL":
        series = calc_boll(closes, used_params["period"], used_params["std"])
        recent_points = last_valid_points(times, series, recent_n)
        latest = recent_points[-1] if recent_points else None
        if latest and closes:
            last_close = closes[-1]
            upper = latest.get("upper")
            lower = latest.get("lower")
            if isinstance(upper, (int, float)) and last_close >= upper:
                signal = "touch_upper"
            elif isinstance(lower, (int, float)) and last_close <= lower:
                signal = "touch_lower"
    elif indicator_name == "KDJ":
        series = calc_kdj(
            highs,
            lows,
            closes,
            used_params["n"],
            used_params["k"],
            used_params["d"],
        )
        recent_points = last_valid_points(times, series, recent_n)
        latest = recent_points[-1] if recent_points else None
        if latest:
            j_value = latest.get("j")
            if isinstance(j_value, (int, float)):
                if j_value >= 100:
                    signal = "overbought"
                elif j_value <= 0:
                    signal = "oversold"
    else:
        # VOLUME：量能 + 量均线 + 可选持仓
        ma_period = used_params["ma_period"]
        volume_floats = [float(value) for value in volumes]
        vol_ma = calc_sma(volume_floats, ma_period)
        combined: list[dict[str, Any] | None] = []
        for index in range(len(times)):
            combined.append(
                {
                    "volume": volumes[index],
                    "volume_ma": vol_ma[index],
                    "open_interest": open_interests[index],
                    "close": closes[index],
                }
            )
        recent_points = last_valid_points(times, combined, recent_n)
        latest = recent_points[-1] if recent_points else None
        if latest and isinstance(latest.get("volume_ma"), (int, float)):
            vol = float(latest.get("volume") or 0)
            vma = float(latest["volume_ma"])
            if vma > 0 and vol >= vma * 1.5:
                signal = "volume_surge"
            elif vma > 0 and vol <= vma * 0.5:
                signal = "volume_shrink"

    if latest is None or (indicator_name == "VOLUME" and latest.get("volume_ma") is None):
        return {"success": False, "error_code": "insufficient_market_data", "error": "本盘行情不足以形成所请求指标的有效窗口", "trading_mode": trading_mode, "lookback": len(closes), "params": used_params}
    return {
        "success": True,
        "trading_mode": trading_mode,
        "input_source": kline_result.get("source"),
        "input_freshness": kline_result.get("freshness"),
        "input_read_at": kline_result.get("read_at"),
        "input_includes_forming": bool(kline_result.get("realtime_merged")),
        "input_origin_verification": kline_result.get("origin_verification"),
        "usage_limit": kline_result.get("usage_limit"),
        "symbol": kline_result.get("symbol"),
        "period": period_clean,
        "indicator": indicator_name,
        "params": used_params,
        "lookback": len(closes),
        "latest": latest,
        "signal": signal,
        "recent_points": recent_points,
        "price_last": closes[-1] if closes else None,
        "time_last": times[-1] if times else None,
    }
