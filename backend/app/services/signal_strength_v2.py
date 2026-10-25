"""强弱指标 V2「形态档」计算内核 —— 与前端 lib/strength-v2.ts 逐位同源

六档双向信号（bottom_exhaust / top_exhaust / rebound / breakdown /
continuation_long / continuation_short）。同一组 bars 两端必须得到逐位
相同的结果，由 tests/test_signal_strength_v2.py 与前端
strength-v2.test.mjs / strength-v2-spec.test.mjs 共读
fixtures/strength_v2_cases.json 守护。算法规格见
discuss/2026-08-31-强弱指标V2形态档设计.md 第四/五节。

## 与 V1（signal_strength.py）的关键差异（V2 文档第三节「地基」）

- V2 的一切判定（S 值、ΔS、body、位置、regime）一律使用**显示序列**
  （二次平滑 close，即 p_close），不读一次平滑 s_close —— 判定序列与
  图上柱子天然同一条；输出点管线与 V1 逐位同构（含首根 open 取 raw）
- 状态机与六档优先级判定见 signal_strength_v2_regime.py（本文件守住
  300 行上限的拆分）

## 数值口径（与前端逐位一致的关键）

- 全程 float64 不做任何 round；仅在产出点/信号值时 half-up 保留 2 位
  （`math.floor(x*100+0.5)/100`，**勿用内置 round**——银行家舍入）
- 平滑表达式与前端逐字符对齐：`alpha * raw + (1 - alpha) * prev`

严格因果：只用 [0, i] 数据；序列末根视为形成中，其信号 pending=True
（未收盘确认，可能消失）；已收盘 bar 的信号永不撤销。
"""

from __future__ import annotations

import math
from typing import Any, TypedDict

from app.services.signal_strength import StrengthPoint
from app.services.signal_strength_v2_regime import (
    _round2,
    advance_regime,
    init_regime_state,
    judge_tiers,
)


def calc_atr(klines: list[dict[str, Any]], period: int) -> list[float | None]:
    """Wilder ATR，与前端一致。"""
    n = len(klines)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period + 1:
        return out

    tr: list[float] = [0.0] * n
    tr[0] = float(klines[0]["high"]) - float(klines[0]["low"])
    for i in range(1, n):
        h = float(klines[i]["high"])
        l = float(klines[i]["low"])
        pc = float(klines[i - 1]["close"])
        tr[i] = max(h - l, abs(h - pc), abs(l - pc))

    total = sum(tr[1 : period + 1])
    atr = total / period
    out[period] = atr
    for i in range(period + 1, n):
        atr = (atr * (period - 1) + tr[i]) / period
        out[i] = atr
    return out


class StrengthV2Params(TypedDict):
    period: int  # 归一化窗口（2-200），默认 14
    smooth: int  # 一次平滑周期（1-50），默认 3
    smooth2: int  # 二次平滑周期（1-50），默认 2 —— V2 判定与显示同用此序列
    continuation_band: int  # 多空滞回带半宽（1-25），默认 5
    exhaust_window: int  # 动能衰竭窗口（2-20），默认 3
    shrink_ratio: float  # 收缩比上限（0.1-1），默认 0.6
    flat_eps: float  # 走平实体上限（0.1-20），默认 2.0
    zone_drop: float  # 段内相对位置容差（1-50）：距段峰/段谷 ≤ 此值，默认 10
    price_buffer_atr_mult: float  # 价格确认 ATR 缓冲倍数（0-5），默认 0.3
    atr_period: int  # ATR 周期（2-200），默认 14；预热段中继/衰竭不触发
    cooldown: int  # 同档冷却根数（0-200），默认 3


class StrengthV2Signal(TypedDict):
    time: str
    index: int  # 触发 bar 在输入序列中的下标
    kind: str  # bottom_exhaust | top_exhaust | rebound | breakdown | continuation_long | continuation_short
    value: float  # 触发时的强弱值（= 显示序列 close，保留 2 位）
    pending: bool  # 末根（形成中）信号，未收盘确认


class StrengthV2Result(TypedDict):
    points: list[StrengthPoint]
    signals: list[StrengthV2Signal]
    # 每个输出点处理完 ①② 后的 regime（none | up | down；与 points 等长）
    regimes: list[str]


DEFAULT_STRENGTH_V2_PARAMS = StrengthV2Params(
    period=14,
    smooth=3,
    smooth2=2,
    continuation_band=5,
    exhaust_window=4,
    shrink_ratio=0.45,
    flat_eps=1.2,
    zone_drop=10.0,
    price_buffer_atr_mult=0.3,
    atr_period=14,
    cooldown=8,
)

# 参数名别名表：camelCase（前端图表配置/fixture）与 snake_case 双口径归一
_PARAM_KEYS: dict[str, str] = {
    "period": "period",
    "smooth": "smooth",
    "smooth2": "smooth2",
    "continuationBand": "continuation_band",
    "continuation_band": "continuation_band",
    "exhaustWindow": "exhaust_window",
    "exhaust_window": "exhaust_window",
    "shrinkRatio": "shrink_ratio",
    "shrink_ratio": "shrink_ratio",
    "flatEps": "flat_eps",
    "flat_eps": "flat_eps",
    "zoneDrop": "zone_drop",
    "zone_drop": "zone_drop",
    "priceBufferAtrMult": "price_buffer_atr_mult",
    "price_buffer_atr_mult": "price_buffer_atr_mult",
    "atrPeriod": "atr_period",
    "atr_period": "atr_period",
    "cooldown": "cooldown",
}


def normalize_strength_v2_params(raw: dict[str, Any] | None) -> StrengthV2Params:
    """camelCase（前端/fixture 口径）→ snake_case；缺省回落默认值。

    供图表配置解析与量化策略层共用；未知键忽略，不抛错。
    """
    params = dict(DEFAULT_STRENGTH_V2_PARAMS)
    if not raw:
        return params
    for camel, snake in _PARAM_KEYS.items():
        if camel in raw and raw[camel] is not None:
            params[snake] = raw[camel]  # type: ignore[literal-required]
    return params


def calc_strength_v2(bars: list[dict[str, Any]], params: StrengthV2Params) -> StrengthV2Result:
    """计算强弱指标 V2。输出点与判定同用二次平滑序列（V2 文档第三节）。

    两套下标约定（4.3.1）：k 为 S 序列下标，i = first + k 为 bar 下标；
    ATR/low/high 一律用 bar 下标。
    """
    n = len(bars)
    first = params["period"] - 1
    if n <= first:
        return StrengthV2Result(points=[], signals=[], regimes=[])

    alpha = 1 / params["smooth"]
    beta = 1 / params["smooth2"]
    count = n - first

    # —— 管线（与 V1 signal_strength.py 逐位同构）：raw → 一次 → 二次 → 点 ——
    s_close: list[float] = [0.0] * count
    p_close: list[float] = [0.0] * count
    points: list[StrengthPoint] = []

    for k in range(count):
        i = first + k
        bar = bars[i]
        # 归一化窗口 [i-period+1, i]：range==0（一字板/极端停滞）取 50
        hh = -math.inf
        ll = math.inf
        for j in range(i - params["period"] + 1, i + 1):
            if bars[j]["high"] > hh:
                hh = bars[j]["high"]
            if bars[j]["low"] < ll:
                ll = bars[j]["low"]
        price_range = hh - ll
        if price_range == 0:
            raw_close = 50.0
            raw_high = 50.0
            raw_low = 50.0
            raw_open = 50.0
        else:
            raw_close = ((bar["close"] - ll) / price_range) * 100
            raw_high = ((bar["high"] - ll) / price_range) * 100
            raw_low = ((bar["low"] - ll) / price_range) * 100
            raw_open = ((bar["open"] - ll) / price_range) * 100

        # 一次平滑：主序列递归；open/high/low 复用同一个上一状态 S[i-1]
        if k == 0:
            s_c = raw_close
            s_h = raw_high
            s_l = raw_low
        else:
            prev = s_close[k - 1]
            s_c = alpha * raw_close + (1 - alpha) * prev
            s_h = alpha * raw_high + (1 - alpha) * prev
            s_l = alpha * raw_low + (1 - alpha) * prev
        s_close[k] = s_c

        # 二次平滑：同结构再走一遍；首根直接承接一次平滑值（open 取 raw）
        if k == 0:
            p_c = s_c
            points.append(
                StrengthPoint(
                    time=bar["time"],
                    open=_round2(raw_open),
                    high=_round2(s_h),
                    low=_round2(s_l),
                    close=_round2(s_c),
                )
            )
        else:
            prev_p = p_close[k - 1]
            p_c = beta * s_c + (1 - beta) * prev_p
            p_h = beta * s_h + (1 - beta) * prev_p
            p_l = beta * s_l + (1 - beta) * prev_p
            # 蜡烛连续：open[i] = 二次平滑 close[i-1]
            points.append(
                StrengthPoint(
                    time=bar["time"],
                    open=_round2(prev_p),
                    high=_round2(p_h),
                    low=_round2(p_l),
                    close=_round2(p_c),
                )
            )
        p_close[k] = p_c

    # —— 状态机 + 六档信号（判定一律用 p_close；ATR 为 bar 下标，预热为 None）——
    atr_arr = calc_atr(bars, params["atr_period"])
    signals: list[StrengthV2Signal] = []
    regimes: list[str] = []
    rstate = init_regime_state()

    for k in range(count):
        i = first + k
        s = p_close[k]
        # 价格确认用「本根之前」的段内极值快照（不含本根自身影线，4.6；
        # 若含本根，low[i] >= min(…, low[i]) - buf 恒成立，价格拒绝无法构造）
        pullback_low_before = rstate["pullback_low_price"]
        rebound_high_before = rstate["rebound_high_price"]

        regime_flipped = advance_regime(rstate, s, bars[i], params)
        regimes.append(rstate["regime"])
        if k < 2:
            continue

        signal = judge_tiers(
            rstate,
            s=s,
            d=s - p_close[k - 1],
            dprev=p_close[k - 1] - p_close[k - 2],
            bar=bars[i],
            p_close=p_close,
            atr=atr_arr[i],
            pullback_low_before=pullback_low_before,
            rebound_high_before=rebound_high_before,
            k=k,
            i=i,
            regime_flipped=regime_flipped,
            params=params,
            is_last=i == n - 1,
        )
        if signal is not None:
            signals.append(signal)

    return StrengthV2Result(points=points, signals=signals, regimes=regimes)
