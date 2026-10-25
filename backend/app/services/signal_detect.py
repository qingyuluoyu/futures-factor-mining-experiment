"""信号判定纯函数 —— 仅认「上一根已收盘 K 线」上新出现的信号

- 当前未走完的 forming bar 不参与信号判定
- MACD：仅在该收盘 K 上完成交叉
- 做多/做空：该收盘 K 首次满足，且再前一根不满足
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any

from app.services.ai.tools.calc import calc_macd, calc_sma
from app.services.trading_hours import beijing_now

_BJ = timezone(timedelta(hours=8))

_PERIOD_LABEL: dict[str, str] = {
    "1d": "日线",
    "60m": "60分",
    "30m": "30分",
    "15m": "15分",
    "5m": "5分",
}

_PERIOD_MINUTES: dict[str, int] = {
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "60m": 60,
}


def period_label(period: str) -> str:
    """周期中文名。"""
    return _PERIOD_LABEL.get(period, period)


def closes_from_bars(bars: list[dict[str, Any]]) -> list[float]:
    """提取收盘价。"""
    out: list[float] = []
    for bar in bars:
        try:
            if bar.get("close") is not None:
                out.append(float(bar["close"]))
        except (TypeError, ValueError):
            continue
    return out


def _parse_bar_time(value: Any) -> datetime | None:
    """解析 bar 时间。"""
    if value is None:
        return None
    text = str(value).strip().replace("T", " ").replace("Z", "")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            chunk = text[:19] if len(text) >= 19 else text
            return datetime.strptime(chunk, fmt).replace(tzinfo=_BJ)
        except ValueError:
            continue
    return None


def resolve_signal_bar_index(
    bars: list[dict[str, Any]],
    period: str,
    now: datetime | None = None,
) -> int | None:
    """确定用于信号的「已收盘」K 线下标。

    - 日线：最后一根日期 == 今天，且处于 15:00 前（或 21:00 后夜盘）→ forming，用 -2；
      15:00–21:00 之间最后一根（今天）视为已收盘，用 -1。
    - 分钟线：若 now 仍在最后一根周期内 → forming 用 -2，否则用 -1。
    """
    n = len(bars)
    if n < 1:
        return None
    if n == 1:
        return 0

    bj = (now or beijing_now()).astimezone(_BJ)
    last_t = _parse_bar_time(bars[-1].get("time"))
    if last_t is None:
        return n - 2

    if period == "1d":
        last_date = last_t.date()
        today = bj.date()
        if last_date < today:
            # 历史最后一根已是过去日期 → 已收盘
            return n - 1
        if last_date > today:
            return n - 2
        # last_date == today：日盘 15:00 前或夜盘 21:00 后仍属未收盘交易日
        t = bj.time()
        if time(15, 0) <= t < time(21, 0):
            return n - 1  # 日盘收盘后、夜盘前
        return n - 2  # 日盘中或夜盘中

    minutes = _PERIOD_MINUTES.get(period)
    if minutes is None:
        return n - 2
    # 分钟 bar 的 time 是周期起点，结束 = 起点 + minutes
    bar_end = last_t + timedelta(minutes=minutes)
    if bj < bar_end:
        return n - 2  # 仍在本根内
    return n - 1  # 本根已走完


def detect_macd_cross_at(
    closes: list[float],
    signal_index: int,
) -> dict[str, Any] | None:
    """在指定已收盘 index 上是否刚完成 MACD 交叉。"""
    if signal_index < 1 or signal_index >= len(closes):
        return None
    if len(closes) < 30:
        return None
    points = calc_macd(closes, 12, 26, 9)
    if signal_index >= len(points):
        return None
    prev = points[signal_index - 1]
    cur = points[signal_index]
    dif0, dea0 = prev.get("dif"), prev.get("dea")
    dif1, dea1 = cur.get("dif"), cur.get("dea")
    if None in (dif0, dea0, dif1, dea1):
        return None
    f0, e0, f1, e1 = float(dif0), float(dea0), float(dif1), float(dea1)
    if f0 <= e0 and f1 > e1 and (f1 - e1) > 1e-6:
        return {
            "kind": "macd_golden",
            "label": "MACD金叉",
            "direction": "long",
            "detail": f"收盘K线 DIF上穿DEA（{f1:.4f}>{e1:.4f}）",
        }
    if f0 >= e0 and f1 < e1 and (e1 - f1) > 1e-6:
        return {
            "kind": "macd_death",
            "label": "MACD死叉",
            "direction": "short",
            "detail": f"收盘K线 DIF下穿DEA（{f1:.4f}<{e1:.4f}）",
        }
    return None


def detect_macd_cross(closes: list[float]) -> dict[str, Any] | None:
    """兼容旧调用：默认最后一根为 forming，信号在 -2。"""
    if len(closes) < 3:
        return None
    return detect_macd_cross_at(closes, len(closes) - 2)


def _bias_at(
    closes: list[float],
    ma5: list[float | None],
    ma10: list[float | None],
    ma20: list[float | None],
    index: int,
) -> str | None:
    """指定 bar 的多空状态。"""
    if index < 3 or index >= len(closes):
        return None
    a5, a10, a20 = ma5[index], ma10[index], ma20[index]
    if None in (a5, a10, a20):
        return None
    f5, f10, f20 = float(a5), float(a10), float(a20)
    close = closes[index]
    start = max(0, index - 2)
    window = closes[start : index + 1]
    up_n = sum(1 for i in range(1, len(window)) if window[i] > window[i - 1])
    down_n = sum(1 for i in range(1, len(window)) if window[i] < window[i - 1])
    if close > f20 and f5 > f10 > f20 and up_n >= 2:
        return "long"
    if close < f20 and f5 < f10 < f20 and down_n >= 2:
        return "short"
    return None


def detect_kline_bias_at(
    closes: list[float],
    period: str,
    signal_index: int,
) -> dict[str, Any] | None:
    """在 signal_index 上是否「新出现」做多/做空。"""
    if signal_index < 1 or len(closes) < 32:
        return None
    ma5 = calc_sma(closes, 5)
    ma10 = calc_sma(closes, 10)
    ma20 = calc_sma(closes, 20)
    now_bias = _bias_at(closes, ma5, ma10, ma20, signal_index)
    if now_bias is None:
        return None
    if _bias_at(closes, ma5, ma10, ma20, signal_index - 1) == now_bias:
        return None

    pl = period_label(period)
    close = closes[signal_index]
    a20 = ma20[signal_index]
    f20 = float(a20) if a20 is not None else close
    if now_bias == "long":
        return {
            "kind": "kline_long",
            "label": f"{pl}做多信号",
            "direction": "long",
            "detail": (
                f"收盘K线新满足：收盘{close:.2f}>MA20({f20:.2f}) "
                f"且 MA5>MA10>MA20"
            ),
        }
    return {
        "kind": "kline_short",
        "label": f"{pl}做空信号",
        "direction": "short",
        "detail": (
            f"收盘K线新满足：收盘{close:.2f}<MA20({f20:.2f}) "
            f"且 MA5<MA10<MA20"
        ),
    }


def detect_kline_bias(closes: list[float], period: str) -> dict[str, Any] | None:
    """兼容旧调用：默认 forming=最后一根。"""
    if len(closes) < 3:
        return None
    return detect_kline_bias_at(closes, period, len(closes) - 2)
