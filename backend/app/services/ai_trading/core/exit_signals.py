"""平仓标准扩展 —— 技术指标死叉/金叉平仓 + 因子阈值平仓（纯函数，无 IO）

与盈亏%平仓同级的硬规则：在每根新收盘 K 线上检查，命中即强制平仓，
先于策略信号 / AI 决策执行（优先级：止损 → 盈亏%止盈 → 最长持仓 → 本模块）。

指标穿越口径（与 strategies/macd_cross.py / ma_cross.py / kdj_cross.py 一致）：
- 死叉 = 前一根 快线≥慢线 且 最新一根 快线<慢线
- 金叉 = 前一根 快线≤慢线 且 最新一根 快线>慢线
方向对称：持多仓遇死叉平多；持空仓遇金叉平空。

因子阈值平仓（pos = tanh 后的因子仓位意图，∈(-1,1)；>+0.3 偏多 / <-0.3 偏空）：
- decay 动能衰减（默认）：持多仓 pos ≤ +long_threshold 平多；持空仓 pos ≥ -short_threshold 平空
- reach 达到即平：持多仓 pos ≥ +long_threshold 平多；持空仓 pos ≤ -short_threshold 平空
  （过热离场；此时新开仓同步抑制，防止开完立刻平的抖动）
阈值按模式钳制：decay 必须 < 开仓线 0.3，reach 必须 > 0.35，否则开平互踩。
"""

from __future__ import annotations

import math
from typing import Any

from app.services.ai.tools.calc import calc_ema, calc_kdj, calc_macd, calc_sma
from app.services.ai_trading.strategies.kdj_cross import normalize_kdj_params
from app.services.ai_trading.strategies.ma_cross import (
    normalize_ma_params,
)
from app.services.ai_trading.strategies.macd_cross import (
    normalize_macd_params,
)

INDICATOR_EXIT_KINDS: tuple[str, ...] = (
    "macd_cross",
    "ma_cross",
    "kdj_cross",
)
INDICATOR_EXIT_LABELS: dict[str, str] = {
    "macd_cross": "MACD",
    "ma_cross": "均线",
    "kdj_cross": "KDJ",
}
MAX_INDICATOR_EXITS = 3

FACTOR_EXIT_MODES: tuple[str, ...] = ("decay", "reach")
# 防抖边界：decay 必须低于因子开仓线 0.3（ENTRY_THRESHOLD），reach 必须明显高于开仓线
DECAY_THRESHOLD_RANGE = (0.01, 0.29)
REACH_THRESHOLD_RANGE = (0.35, 0.99)
DECAY_THRESHOLD_DEFAULT = 0.15
REACH_THRESHOLD_DEFAULT = 0.80


def _pos_qty_dir(position: dict[str, Any] | None) -> tuple[int, str]:
    qty = int(
        (position or {}).get("available_quantity")
        or (position or {}).get("quantity")
        or 0
    )
    direction = str((position or {}).get("direction") or "")
    return qty, direction


def normalize_indicator_exits(raw: Any) -> list[dict[str, Any]]:
    """规范化技术指标平仓条件：kind 白名单、参数钳制、kind+参数去重、最多 3 条"""
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for item in raw[:MAX_INDICATOR_EXITS]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip().lower()
        if kind not in INDICATOR_EXIT_KINDS:
            continue
        params = item.get("params")
        if not isinstance(params, dict):
            params = {}
        if kind == "macd_cross":
            params = normalize_macd_params(params)
        elif kind == "ma_cross":
            params = normalize_ma_params(params)
        else:
            # KDJ 平仓不做超买超卖区过滤（死叉即平），剔除 zone 相关键
            p = normalize_kdj_params(params)
            params = {
                k: v
                for k, v in p.items()
                if k not in ("use_zone", "oversold", "overbought")
            }
        key = (kind, tuple(sorted(params.items())))
        if key in seen:
            continue
        seen.add(key)
        out.append({"kind": kind, "params": params})
    return out


def normalize_factor_exit(
    raw: Any,
    entry_threshold: float | None = None,
) -> dict[str, Any] | None:
    """规范化因子阈值平仓配置；非法输入返回 None（条件不生效）

    阈值为正数存储，方向按多空各自施加符号；越界值钳到该模式合法区间。
    entry_threshold：因子开仓线（任务可调）。提供时联动钳制防止开平互踩：
    decay 必须 < 开仓线，reach 必须 > 开仓线。
    """
    if not isinstance(raw, dict) or not raw:
        # None / 非法输入 / 空 dict 均视为未配置该条件
        return None
    mode = str(raw.get("mode") or "decay").strip().lower()
    if mode not in FACTOR_EXIT_MODES:
        mode = "decay"
    lo, hi = (
        DECAY_THRESHOLD_RANGE if mode == "decay" else REACH_THRESHOLD_RANGE
    )
    if entry_threshold is not None:
        try:
            entry = float(entry_threshold)
        except (TypeError, ValueError):
            entry = None
        if entry is not None and math.isfinite(entry):
            if mode == "decay":
                hi = max(lo, min(hi, entry - 0.01))
            else:
                lo = min(hi, max(lo, entry + 0.05))
    default = (
        DECAY_THRESHOLD_DEFAULT if mode == "decay" else REACH_THRESHOLD_DEFAULT
    )

    def _threshold(key: str) -> float:
        try:
            value = float(raw.get(key))
        except (TypeError, ValueError):
            return default
        if not math.isfinite(value):
            return default
        return round(min(hi, max(lo, abs(value))), 4)

    return {
        "mode": mode,
        "long_threshold": _threshold("long_threshold"),
        "short_threshold": _threshold("short_threshold"),
    }


def _closes(bars: list[dict[str, Any]]) -> list[float]:
    out: list[float] = []
    for bar in bars:
        try:
            out.append(float(bar.get("close") or 0))
        except (TypeError, ValueError):
            out.append(0.0)
    return out


def _ohlc(bars: list[dict[str, Any]]) -> tuple[list[float], list[float], list[float]]:
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    for bar in bars:
        try:
            h = float(bar.get("high") or 0)
            low = float(bar.get("low") or 0)
            c = float(bar.get("close") or 0)
        except (TypeError, ValueError):
            h = low = c = 0.0
        if h <= 0:
            h = c
        if low <= 0:
            low = c
        highs.append(h)
        lows.append(low)
        closes.append(c)
    return highs, lows, closes


def _detect_cross(
    prev_fast: float, prev_slow: float, now_fast: float, now_slow: float
) -> tuple[bool, bool]:
    """返回 (golden, death)，口径与策略层一致"""
    golden = prev_fast <= prev_slow and now_fast > now_slow
    death = prev_fast >= prev_slow and now_fast < now_slow
    return golden, death


def check_indicator_exit_rules(
    indicator_exits: Any,
    bars: list[dict[str, Any]],
    position: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """逐条件在最后一根已收盘 bar 上检查穿越，命中返回强制平仓动作，否则 None

    bars 应为已收盘 K 线序列（engine 的 signal_bars）；
    持多仓遇死叉平多、持空仓遇金叉平空，方向对称。
    """
    exits = normalize_indicator_exits(indicator_exits)
    if not exits or len(bars) < 3:
        return None
    pos_qty, pos_dir = _pos_qty_dir(position)
    if pos_qty <= 0 or pos_dir not in ("long", "short"):
        return None

    closes = _closes(bars)
    for item in exits:
        kind = item["kind"]
        params = item.get("params") or {}
        if kind == "macd_cross":
            p = normalize_macd_params(params)
            need = int(p["slow_period"]) + int(p["signal_period"]) + 2
            if len(bars) < need:
                continue
            points = calc_macd(
                closes,
                int(p["fast_period"]),
                int(p["slow_period"]),
                int(p["signal_period"]),
            )
            prev_pt, now_pt = points[-2], points[-1]
            d0, e0 = prev_pt.get("dif"), prev_pt.get("dea")
            d1, e1 = now_pt.get("dif"), now_pt.get("dea")
            if None in (d0, e0, d1, e1):
                continue
            golden, death = _detect_cross(float(d0), float(e0), float(d1), float(e1))
            detail = (
                f"MACD({p['fast_period']}/{p['slow_period']}/{p['signal_period']}) "
                f"DIF{'上穿' if golden else '下穿'}DEA"
            )
        elif kind == "ma_cross":
            p = normalize_ma_params(params)
            if len(bars) < int(p["slow_period"]) + 2:
                continue
            if str(p["ma_type"]) == "ema":
                fast_series = calc_ema(closes, int(p["fast_period"]))
                slow_series = calc_ema(closes, int(p["slow_period"]))
            else:
                fast_series = calc_sma(closes, int(p["fast_period"]))
                slow_series = calc_sma(closes, int(p["slow_period"]))
            f0, f1 = fast_series[-2], fast_series[-1]
            s0, s1 = slow_series[-2], slow_series[-1]
            if None in (f0, f1, s0, s1):
                continue
            golden, death = _detect_cross(float(f0), float(s0), float(f1), float(s1))
            detail = (
                f"均线{str(p['ma_type']).upper()}"
                f"({p['fast_period']}/{p['slow_period']}) "
                f"快线{'上穿' if golden else '下穿'}慢线"
            )
        elif kind == "kdj_cross":
            p = normalize_kdj_params(params)
            need = int(p["n_period"]) + 2
            if len(bars) < need:
                continue
            highs, lows, kcloses = _ohlc(bars)
            points = calc_kdj(
                highs,
                lows,
                kcloses,
                int(p["n_period"]),
                int(p["k_period"]),
                int(p["d_period"]),
            )
            prev_pt, now_pt = points[-2], points[-1]
            k0, d0 = prev_pt.get("k"), prev_pt.get("d")
            k1, d1 = now_pt.get("k"), now_pt.get("d")
            if None in (k0, d0, k1, d1):
                continue
            golden, death = _detect_cross(float(k0), float(d0), float(k1), float(d1))
            detail = (
                f"KDJ({p['n_period']},{p['k_period']},{p['d_period']}) "
                f"K{'上穿' if golden else '下穿'}D"
            )
        else:
            continue

        if pos_dir == "long" and death:
            return {
                "action": "close",
                "reason": f"指标平仓：{detail} 平多",
                "trigger_type": "indicator_close",
                "quantity": pos_qty,
                "context": {"exit_kind": kind},
            }
        if pos_dir == "short" and golden:
            return {
                "action": "close",
                "reason": f"指标平仓：{detail} 平空",
                "trigger_type": "indicator_close",
                "quantity": pos_qty,
                "context": {"exit_kind": kind},
            }
    return None


def evaluate_factor_exit_rules(
    factor_exit: Any,
    pos: float | None,
    position: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """按模式比较因子仓位意图 pos 与阈值，命中返回强制平仓动作，否则 None"""
    cfg = normalize_factor_exit(factor_exit)
    if cfg is None or pos is None or not math.isfinite(float(pos)):
        return None
    pos_qty, pos_dir = _pos_qty_dir(position)
    if pos_qty <= 0 or pos_dir not in ("long", "short"):
        return None
    pos_f = float(pos)
    lt = float(cfg["long_threshold"])
    st = float(cfg["short_threshold"])

    if cfg["mode"] == "decay":
        if pos_dir == "long" and pos_f <= lt:
            return {
                "action": "close",
                "reason": f"因子平仓：动能衰减 pos={pos_f:.2f} ≤ {lt:.2f} 平多",
                "trigger_type": "factor_exit",
                "quantity": pos_qty,
                "context": {"exit_mode": "decay"},
            }
        if pos_dir == "short" and pos_f >= -st:
            return {
                "action": "close",
                "reason": f"因子平仓：动能衰减 pos={pos_f:.2f} ≥ -{st:.2f} 平空",
                "trigger_type": "factor_exit",
                "quantity": pos_qty,
                "context": {"exit_mode": "decay"},
            }
    else:  # reach 达到即平（过热离场）
        if pos_dir == "long" and pos_f >= lt:
            return {
                "action": "close",
                "reason": f"因子平仓：达到阈值 pos={pos_f:.2f} ≥ {lt:.2f} 平多",
                "trigger_type": "factor_exit",
                "quantity": pos_qty,
                "context": {"exit_mode": "reach"},
            }
        if pos_dir == "short" and pos_f <= -st:
            return {
                "action": "close",
                "reason": f"因子平仓：达到阈值 pos={pos_f:.2f} ≤ -{st:.2f} 平空",
                "trigger_type": "factor_exit",
                "quantity": pos_qty,
                "context": {"exit_mode": "reach"},
            }
    return None


def normalize_close_rules_exits(
    close_rules: dict[str, Any],
    entry_threshold: float | None = None,
) -> dict[str, Any]:
    """规范化 close_rules 中的指标/因子平仓扩展字段；返回新 dict

    空值直接移除该键，保持 JSONB 干净（与 pnl_pct 缺省即不生效的口径一致）。
    entry_threshold：因子开仓线（factor 任务或 decision 挂 factor 策略时传入）。
    """
    out = dict(close_rules or {})
    if "indicator_exits" in out:
        exits = normalize_indicator_exits(out.get("indicator_exits"))
        if exits:
            out["indicator_exits"] = exits
        else:
            out.pop("indicator_exits", None)
    if "factor_exit" in out:
        cfg = normalize_factor_exit(out.get("factor_exit"), entry_threshold)
        if cfg:
            out["factor_exit"] = cfg
        else:
            out.pop("factor_exit", None)
    return out


def factor_entry_suppressed(factor_exit: Any, pos: float | None) -> bool:
    """reach 模式入场抑制：|pos| 已达离场阈值时不再开新仓（防止开完立刻平的抖动）"""
    cfg = normalize_factor_exit(factor_exit)
    if cfg is None or pos is None or cfg["mode"] != "reach":
        return False
    try:
        pos_f = float(pos)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(pos_f):
        return False
    return pos_f >= float(cfg["long_threshold"]) or pos_f <= -float(
        cfg["short_threshold"]
    )
