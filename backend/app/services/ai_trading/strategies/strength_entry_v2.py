"""强弱形态 V2 进场策略（strength_entry_v2）—— 六档信号双向，纯规则

与前端图表「强弱 V2 形态档」同口径（复用 signal_strength_v2.calc_strength_v2，
含方案一重定义后的衰竭两档）。信号语义（V2 设计文档第七节，定死）：
- 开多：多侧信号（底部衰竭 / 反弹确认 / 多头中继）任一在新鲜度窗口内触发
- 开空：空侧信号（顶部衰竭 / 破位确认 / 空头中继）任一在新鲜度窗口内触发
- 平多：出现任一空侧信号（反向先平），或 regime 已翻转为 down（状态出场，
  兜底信号过期后仍持多的场景）
- 平空：出现任一侧多信号（反向先平），或 regime 已翻转为 up
- side_mode：long_only 只取多侧；short_only 只取空侧；both 全取
- 新鲜度窗口 = cooldown 根（沿用 V1 strength_entry 设计）
- 只在已收盘 bar 上评估（bars 末根应为已收盘 bar；内核 pending 标记面向
  图表实时链路，此处末根信号按已收盘对待）

strategy_params 即内核参数（snake_case，兼容图表 camelCase，双口径归一见
signal_strength_v2.normalize_strength_v2_params），无额外策略参数——
出场由信号与 regime 状态驱动，不用固定阈值。
"""

from __future__ import annotations

from typing import Any

from app.services.ai_trading.strategies.position_handlers import (
    handle_bearish,
    handle_bullish,
    position_qty_dir,
    signal_result,
)
from app.services.signal_strength_v2 import (
    calc_strength_v2,
    normalize_strength_v2_params,
)

_SNAP = "strength_entry_v2_snapshot"

_LONG_KINDS = ("bottom_exhaust", "rebound", "continuation_long")
_SHORT_KINDS = ("top_exhaust", "breakdown", "continuation_short")

_KIND_LABEL = {
    "bottom_exhaust": "底部衰竭",
    "top_exhaust": "顶部衰竭",
    "rebound": "反弹确认",
    "breakdown": "破位确认",
    "continuation_long": "多头中继",
    "continuation_short": "空头中继",
}


def normalize_strength_entry_v2_params(raw: dict[str, Any] | None) -> dict[str, Any]:
    """规范化策略参数（即 V2 内核参数；旧字段静默忽略，回落默认）"""
    return normalize_strength_v2_params(raw)


def kind_label_v2(signal: dict[str, Any]) -> str:
    """信号 kind → 中文标签"""
    return _KIND_LABEL.get(str(signal.get("kind")), str(signal.get("kind")))


def _brief(signal: dict[str, Any] | None) -> dict[str, Any] | None:
    """快照用的最新信号摘要（含 kind，便于复盘）"""
    if signal is None:
        return None
    return {
        "kind": signal.get("kind"),
        "index": signal.get("index"),
        "time": signal.get("time"),
        "value": signal.get("value"),
    }


def compute_strength_entry_v2_signal(
    bars: list[dict[str, Any]],
    params: dict[str, Any],
    side_mode: str,
    position: dict[str, Any] | None,
) -> dict[str, Any]:
    """根据 K 线计算强弱形态 V2 信号

    bars 末根应为当前已收盘 bar。返回 signal_result 统一结构，快照键
    strength_entry_v2_snapshot（含 regime 与 last_signal.kind，便于复盘）。
    """
    p = normalize_strength_entry_v2_params(params)

    if len(bars) < int(p["period"]):
        return signal_result(
            "hold",
            f"K线不足（需≥{p['period']}根）",
            0,
            "insufficient_data",
            _SNAP,
            None,
        )

    res = calc_strength_v2(bars, p)
    last_idx = len(bars) - 1
    last_point = res["points"][-1] if res["points"] else None
    strength_now = float(last_point["close"]) if last_point else 50.0
    regime_now = str(res["regimes"][-1]) if res["regimes"] else "none"
    last_signal = res["signals"][-1] if res["signals"] else None

    snapshot: dict[str, Any] = {
        **{k: p[k] for k in p},
        "close": float(bars[-1].get("close") or 0),
        "bar_time": bars[-1].get("time"),
        "strength": strength_now,
        "regime": regime_now,
        "last_signal": None,
        "freshness": None,
    }

    mode = (side_mode or "both").strip().lower()
    pos_qty, pos_dir = position_qty_dir(position)

    # 状态出场（优先于信号路径）：持仓方向与当前 regime 相逆 → 离场。
    # 兜底场景：翻转信号已过新鲜度窗口（stale），但 regime 状态仍逆势持仓
    if pos_qty > 0 and pos_dir == "long" and regime_now == "down":
        snapshot["last_signal"] = _brief(last_signal)
        return signal_result(
            "close",
            f"regime 已翻转为 down（强弱空头态，现值 {strength_now:.2f}），平多",
            0.7,
            "strength_v2_regime_exit_long",
            _SNAP,
            snapshot,
        )
    if pos_qty > 0 and pos_dir == "short" and regime_now == "up":
        snapshot["last_signal"] = _brief(last_signal)
        return signal_result(
            "close",
            f"regime 已翻转为 up（强弱多头态，现值 {strength_now:.2f}），平空",
            0.7,
            "strength_v2_regime_exit_short",
            _SNAP,
            snapshot,
        )

    if last_signal is None:
        return signal_result(
            "hold",
            "尚无进场信号",
            0.3,
            "none",
            _SNAP,
            snapshot,
        )

    sig_idx = int(last_signal["index"])
    freshness = last_idx - sig_idx
    snapshot["last_signal"] = _brief(last_signal)
    snapshot["freshness"] = freshness

    if freshness > int(p["cooldown"]):
        return signal_result(
            "hold",
            f"信号已过期（{freshness}根前的{kind_label_v2(last_signal)}，超出新鲜度窗口{p['cooldown']}根）",
            0.35,
            "stale",
            _SNAP,
            snapshot,
        )

    reason = f"{kind_label_v2(last_signal)} @ 强弱值 {float(last_signal['value']):.2f}"
    kind = str(last_signal["kind"])
    if kind in _LONG_KINDS:
        # 多侧信号：开多 / 平空；long_only 与 both 同路（side_mode 由 handler 裁决）
        return handle_bullish(
            mode,
            pos_qty,
            pos_dir,
            reason,
            _SNAP,
            snapshot,
            f"strength_v2_{kind}",
        )
    # 空侧信号（top_exhaust / breakdown / continuation_short）：开空 / 平多
    return handle_bearish(
        mode,
        pos_qty,
        pos_dir,
        reason,
        _SNAP,
        snapshot,
        f"strength_v2_{kind}",
    )
