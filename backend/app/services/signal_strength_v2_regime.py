"""强弱指标 V2 状态机与六档判定
（discuss/2026-08-31-强弱指标V2形态档设计.md 4.3-4.5，自
signal_strength_v2.py 拆出以守住 300 行上限；与前端
lib/strength-v2-regime.ts 逐位同源）

- advance_regime：① 滞回 regime 更新 + ② 段内极值/回调跟踪
  （新高判定用 >= / <=：翻转根 S==seg_peak_s 必须走重置分支，否则翻转根
  自己的 low 会被误记成回调低点，导致下一根误触发中继 —— 4.3 ② 陷阱）
- judge_tiers：③ 六档优先级判定（转向 / 动能衰竭 / 位置窗口 / 价格确认
  + 每档独立冷却；条件成立但被冷却挡住时继续判定低优先级档）

状态由调用方持有（init_regime_state 新建、单条序列遍历内独占），
两个函数就地更新 state 并返回本根结果，不读写其他全局状态。
"""

from __future__ import annotations

import math
from typing import Any, TypedDict

STRENGTH_V2_KINDS = (
    "bottom_exhaust",
    "top_exhaust",
    "rebound",
    "breakdown",
    "continuation_long",
    "continuation_short",
)


class RegimeState(TypedDict):
    """状态机状态（4.3「状态」清单）"""

    regime: str  # none | up | down
    seg_peak_s: float  # 多头段内 S 的最高（regime=up 时有效）
    seg_trough_s: float  # 空头段内 S 的最低（regime=down 时有效）
    pullback_low_price: float  # 自段峰之后的价格最低 low（+inf = 尚无回调）
    rebound_high_price: float  # 自段谷之后的价格最高 high（-inf = 尚无反弹）
    last_fired: dict[str, float]  # 各档上次触发的 bar 下标（-inf = 未触发）


def _round2(x: float) -> float:
    """half-up 保留 2 位（与前端 Math.round(x*100)/100 同口径；勿用内置 round）"""
    return math.floor(x * 100 + 0.5) / 100


def init_regime_state() -> RegimeState:
    """初始状态：regime=none，段内极值与回调/反弹极值均为未发生"""
    return RegimeState(
        regime="none",
        seg_peak_s=0.0,
        seg_trough_s=0.0,
        pullback_low_price=math.inf,
        rebound_high_price=-math.inf,
        last_fired={kind: -math.inf for kind in STRENGTH_V2_KINDS},
    )


def advance_regime(
    state: RegimeState,
    s: float,
    bar: dict[str, Any],
    params: dict[str, Any],
) -> bool:
    """① regime 更新（滞回）+ ② 段内极值与回调跟踪。

    返回本根是否为 down→up / up→down 的翻转；序列开头 none→x 是初始化，
    返回 False（不出转折信号 —— 4.3 ① 陷阱）。
    """
    regime_flipped = False
    # ① 只有 down→up / up→down 才算「翻转」；翻转时新段开始，回调未发生
    if s > 50 + params["continuation_band"]:
        if state["regime"] != "up":
            was_down = state["regime"] == "down"
            state["regime"] = "up"
            state["seg_peak_s"] = s
            state["pullback_low_price"] = math.inf
            regime_flipped = was_down
    elif s < 50 - params["continuation_band"]:
        if state["regime"] != "down":
            was_up = state["regime"] == "up"
            state["regime"] = "down"
            state["seg_trough_s"] = s
            state["rebound_high_price"] = -math.inf
            regime_flipped = was_up

    # ② 段内极值与回调跟踪（>= / <=：翻转根 S==seg_peak_s 必须走重置分支）
    if state["regime"] == "up":
        if s >= state["seg_peak_s"]:
            state["seg_peak_s"] = s
            state["pullback_low_price"] = math.inf
        else:
            state["pullback_low_price"] = min(state["pullback_low_price"], bar["low"])
    elif state["regime"] == "down":
        if s <= state["seg_trough_s"]:
            state["seg_trough_s"] = s
            state["rebound_high_price"] = -math.inf
        else:
            state["rebound_high_price"] = max(state["rebound_high_price"], bar["high"])
    return regime_flipped


def judge_tiers(
    state: RegimeState,
    s: float,
    d: float,
    dprev: float,
    bar: dict[str, Any],
    p_close: list[float],
    atr: float | None,
    pullback_low_before: float,
    rebound_high_before: float,
    k: int,
    i: int,
    regime_flipped: bool,
    params: dict[str, Any],
    is_last: bool,
) -> dict[str, Any] | None:
    """③ 六档优先级判定（4.5）：命中且冷却放行即返回唯一信号（并更新该档
    last_fired）；条件成立但被冷却挡住时继续判定低优先级档（V1 continue
    位置语义）。无信号返回 None。判定输入的 k/i 预热门槛见 4.7。
    """
    turn_up = dprev <= 0 and d > 0
    turn_down = dprev >= 0 and d < 0

    # 价格确认：ATR 不可用（预热）时中继两档不触发，不是降级放行（4.5）
    price_ok_long = False
    price_ok_short = False
    if atr is not None and atr > 0:
        price_ok_long = bar["low"] >= pullback_low_before - params["price_buffer_atr_mult"] * atr
        price_ok_short = bar["high"] <= rebound_high_before + params["price_buffer_atr_mult"] * atr

    # 动能衰竭（4.4，方案一修订）：窗口不含信号根 [k−m, k−1] / [k−2m, k−m−1]
    # ——平顶/平底结束的首根阴阳柱常是大实体，含进窗会当场摧毁 flat/shrink；
    # 预热 i>=atr_period 且 k>=2m+1（窗口整体前移一根，防 ΔS 负下标）
    exhausted = False
    if k >= 2 * params["exhaust_window"] + 1 and i >= params["atr_period"]:
        m = params["exhaust_window"]
        now_sum = 0.0
        prev_sum = 0.0
        for j in range(k - m, k):
            now_sum += abs(p_close[j] - p_close[j - 1])
        for j in range(k - 2 * m, k - m):
            prev_sum += abs(p_close[j] - p_close[j - 1])
        body_now = now_sum / m
        body_prev = prev_sum / m
        shrinking = body_prev > 0 and body_now / body_prev <= params["shrink_ratio"]
        flat = True
        for j in range(k - m, k):
            if abs(p_close[j] - p_close[j - 1]) > params["flat_eps"]:
                flat = False
                break
        exhausted = shrinking or flat

    # 段内相对位置（4.5，方案一修订）：距段峰/段谷 ≤ zoneDrop 即「顶部/底部附近」；
    # 段峰/谷由 ② 维护（已含本根）。深跌后反弹段价格新高 S 仅 ~60，绝对 75 线不可达
    near_seg_peak = state["regime"] == "up" and state["seg_peak_s"] - s <= params["zone_drop"]
    near_seg_trough = state["regime"] == "down" and s - state["seg_trough_s"] <= params["zone_drop"]

    tiers: list[tuple[str, bool]] = [
        ("rebound", regime_flipped and state["regime"] == "up"),
        ("breakdown", regime_flipped and state["regime"] == "down"),
        (
            "continuation_long",
            state["regime"] == "up"
            and not regime_flipped
            and turn_up
            and math.isfinite(state["pullback_low_price"])
            and price_ok_long,
        ),
        (
            "continuation_short",
            state["regime"] == "down"
            and not regime_flipped
            and turn_down
            and math.isfinite(state["rebound_high_price"])
            and price_ok_short,
        ),
        ("bottom_exhaust", state["regime"] == "down" and near_seg_trough and exhausted and d > 0),
        ("top_exhaust", state["regime"] == "up" and near_seg_peak and exhausted and d < 0),
    ]
    for kind, hit in tiers:
        if hit and i - state["last_fired"][kind] >= params["cooldown"]:
            state["last_fired"][kind] = i
            return {
                "time": bar["time"],
                "index": i,
                "kind": kind,
                "value": _round2(s),
                "pending": is_last,
            }
    return None
