"""强弱进场策略（strength_entry）—— 三档进场信号做多，纯规则

与前端图表「强弱指标」同口径（复用 signal_strength.calc_strength）。
信号语义（见 discuss/2026-08-31-强弱指标设计.md 第八节，定死）：
- 开多：三档信号（超跌>反弹>波段，内核已含同档冷却）任一在新鲜度窗口内
  触发，且 side_mode 允许做多。新鲜度窗口 = cooldown 根（
  的新鲜度设计：任务在信号后数根内评估仍可进场，避免轮询间隙漏单）
- 平多：强弱收盘值 < exit_threshold（默认 50）。退出判定用输出点序列
  （二次平滑后，与图表所见同一曲线；smooth2=1 时即主序列 S）
- 做空：本版本不出空信号；side_mode=short_only 时不产生任何信号
  （返回 hold，不报错）
- 只在已收盘 bar 上评估（bars 末根应为已收盘 bar；
  内核 pending 标记面向图表实时链路，此处末根信号按已收盘对待）

strategy_params（snake_case，兼容图表 camelCase）:
  period / smooth / smooth2 / trend_ma_period   归一化与平滑，默认 14/3/1/10
  swing_threshold / rebound_threshold           进场阈值，默认 50/50
  oversold_level / rebound_lookback             弱区线与回溯，默认 20/10
  deep_level / deep_bars                        极低位与钝化，默认 5/3
  cooldown                                      同档冷却=新鲜度窗口，默认 3
  exit_threshold                                平多阈值，默认 50
"""

from __future__ import annotations

from typing import Any

from app.services.ai_trading.strategies.position_handlers import (
    handle_bullish,
    position_qty_dir,
    signal_result,
)
from app.services.signal_strength import calc_strength, normalize_strength_params

_SNAP = "strength_entry_snapshot"

_ENTRY_LABEL = {
    "swing": "波段进场",
    "rebound": "反弹进场",
    "deep": "超跌进场",
}


def _as_float(src: dict[str, Any], key: str, default: float) -> float:
    value = src.get(key)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_strength_entry_params(raw: dict[str, Any] | None) -> dict[str, Any]:
    """规范化强弱策略参数并 clamp 到安全区间（旧字段静默忽略，回落默认）"""
    params = normalize_strength_params(raw)
    params["exit_threshold"] = max(
        0.0, min(100.0, _as_float(dict(raw or {}), "exit_threshold", 50.0))
    )
    return params


def compute_strength_entry_signal(
    bars: list[dict[str, Any]],
    params: dict[str, Any],
    side_mode: str,
    position: dict[str, Any] | None,
) -> dict[str, Any]:
    """根据 K 线计算强弱进场信号

    bars 末根应为当前已收盘 bar。返回 signal_result 统一结构，
    快照键 strength_entry_snapshot（含最新信号与当前强弱值，便于复盘）。
    """
    p = normalize_strength_entry_params(params)

    if len(bars) < int(p["period"]):
        return signal_result(
            "hold",
            f"K线不足（需≥{p['period']}根）",
            0,
            "insufficient_data",
            _SNAP,
            None,
        )

    res = calc_strength(bars, p)
    last_idx = len(bars) - 1
    last_point = res["points"][-1] if res["points"] else None
    strength_now = float(last_point["close"]) if last_point else 50.0
    last_signal = res["signals"][-1] if res["signals"] else None

    snapshot: dict[str, Any] = {
        **{k: p[k] for k in p},
        "close": float(bars[-1].get("close") or 0),
        "bar_time": bars[-1].get("time"),
        "strength": strength_now,
        "last_signal": None,
        "freshness": None,
    }

    mode = (side_mode or "both").strip().lower()
    pos_qty, pos_dir = position_qty_dir(position)

    # 平多优先于进场：持多且强弱收盘值跌破退出阈值 → 离场
    if pos_qty > 0 and pos_dir == "long" and strength_now < float(p["exit_threshold"]):
        return signal_result(
            "close",
            f"强弱值 {strength_now:.2f} 跌破退出阈值 {p['exit_threshold']}，平多",
            0.7,
            "strength_exit_long",
            _SNAP,
            snapshot,
        )

    # 做空侧：本策略只做多，short_only 不产生任何信号
    if mode == "short_only":
        snapshot["last_signal"] = _brief(last_signal)
        return signal_result(
            "hold",
            "强弱策略只做多：short_only 模式不产生信号",
            0.3,
            "long_only_strategy",
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
            f"信号已过期（{freshness}根前的{kind_label(last_signal)}，超出新鲜度窗口{p['cooldown']}根）",
            0.35,
            "stale",
            _SNAP,
            snapshot,
        )

    return handle_bullish(
        mode,
        pos_qty,
        pos_dir,
        f"{kind_label(last_signal)} @ 强弱值 {float(last_signal['value']):.2f}",
        _SNAP,
        snapshot,
        f"strength_{last_signal['kind']}",
    )


def kind_label(signal: dict[str, Any]) -> str:
    """信号 kind → 中文标签"""
    return _ENTRY_LABEL.get(str(signal.get("kind")), str(signal.get("kind")))


def _brief(signal: dict[str, Any] | None) -> dict[str, Any] | None:
    """快照用的最新信号摘要"""
    if signal is None:
        return None
    return {
        "kind": signal.get("kind"),
        "index": signal.get("index"),
        "time": signal.get("time"),
        "value": signal.get("value"),
    }
