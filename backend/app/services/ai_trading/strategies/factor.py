"""因子公式策略 —— factor_lab 公式 → tanh 仓位意图 → 离散动作

strategy_params:
  factor_tokens: 单公式 token 序列 list[int]，或组合 [[int,...],[int,...],...]
  factor_weights: 组合权重 list[float]（可省，等权；正数，自动归一）
  factor_text:   str 公式中文表达式（可选，展示用）

信号口径（与回测一致）：
  单公式：pos = tanh(factor[-1]) ∈ (-1,1)
  组合：  pos = Σ wᵢ·tanh(factorᵢ[-1]) / Σ wᵢ（无效成员跳过后权重重归一）
  pos > +0.3 → 偏多；pos < -0.3 → 偏空；|pos| < 0.05 且有仓 → 平
"""

from __future__ import annotations

from typing import Any

import numpy as np

from app.services.factor_lab import execute, is_constant, to_text
from app.services.factor_lab.features import feature_matrix

ENTRY_THRESHOLD = 0.3
NEUTRAL = 0.05
MIN_BARS = 30
MAX_GROUPS = 5


def _coerce_tokens(raw: Any) -> list[int]:
    out: list[int] = []
    if not isinstance(raw, (list, tuple)):
        return out
    for t in raw:
        try:
            out.append(int(t))
        except (TypeError, ValueError):
            continue
    return out


def factor_tokens_value_error(groups: list[list[int]]) -> str | None:
    """token 值域校验（创建/更新任务入口用）：None=合法

    越界 token 在内核 execute 恒返回 None——任务会静默退化为
    invalid_factor，创建时显式拒绝；v1 旧编码同样拦截（值域空洞特征，
    与 factor_lab API 的 reject_legacy_tokens 同口径）。
    """
    from app.services.factor_lab.ops import OPS_NAMES
    from app.services.factor_lab.token_encoding import FEAT_OFFSET, looks_like_v1
    from app.services.factor_lab.vm import FEAT_COUNT

    op_hi = FEAT_OFFSET + len(OPS_NAMES)
    for g in groups:
        if not g:
            continue
        if looks_like_v1(g, FEAT_COUNT):
            return "因子公式为旧版 token 编码，请重新生成后再挂载"
        for t in g:
            if t < 0 or (FEAT_COUNT <= t < FEAT_OFFSET) or t >= op_hi:
                return (
                    f"因子 token {t} 超出内核范围"
                    f"(特征 0-{FEAT_COUNT - 1} / 算子 {FEAT_OFFSET}-{op_hi - 1})"
                )
    return None


def normalize_factor_params(
    raw: dict[str, Any] | None, strict: bool = False
) -> dict[str, Any]:
    """规范化因子参数：兼容单公式（扁平 list）与组合（list of lists）

    strict=True（任务创建/更新入口）：token 值域非法直接 400，
    避免挂载后静默退化为 invalid_factor；运行时路径保持宽容。
    """
    src = dict(raw or {})
    raw_tokens = src.get("factor_tokens") or []
    groups: list[list[int]] = []
    if isinstance(raw_tokens, (list, tuple)) and raw_tokens and isinstance(
        raw_tokens[0], (list, tuple)
    ):
        for g in raw_tokens[:MAX_GROUPS]:
            toks = _coerce_tokens(g)
            if toks:
                groups.append(toks)
    else:
        toks = _coerce_tokens(raw_tokens)
        if toks:
            groups.append(toks)

    # 权重：与组数对齐的正浮点数组；缺失/非法回退等权，归一到和为 1
    raw_w = src.get("factor_weights") or []
    weights: list[float] = []
    if isinstance(raw_w, (list, tuple)):
        for w in raw_w[: len(groups)]:
            try:
                fv = float(w)
            except (TypeError, ValueError):
                weights = []
                break
            if fv <= 0 or not np.isfinite(fv):
                weights = []
                break
            weights.append(fv)
    if len(weights) != len(groups):
        weights = [1.0] * len(groups)
    wsum = sum(weights)
    weights = [w / wsum for w in weights] if wsum > 0 else []

    text = str(src.get("factor_text") or "")
    if not text and groups:
        parts = [to_text(g) for g in groups]
        text = parts[0] if len(parts) == 1 else " ⊕ ".join(parts)

    out: dict[str, Any] = {
        "factor_tokens": groups[0] if len(groups) == 1 else groups,
        "factor_text": text,
    }
    # 开仓阈值：因子仓位意图 |pos| 越过该线才给开仓建议（默认 0.3，可调 0.05-0.5）
    try:
        et = float(src.get("entry_threshold"))
    except (TypeError, ValueError):
        et = ENTRY_THRESHOLD
    out["entry_threshold"] = round(max(0.05, min(0.5, et)), 3)
    if len(groups) > 1:
        out["factor_weights"] = [round(w, 6) for w in weights]
    if strict and groups:
        from fastapi import HTTPException

        err = factor_tokens_value_error(groups)
        if err:
            raise HTTPException(status_code=400, detail=err)
    return out


def _split_groups(params: dict[str, Any]) -> tuple[list[list[int]], list[float]]:
    """从 normalize 后的参数取 (公式组, 权重)"""
    tokens = params.get("factor_tokens")
    if isinstance(tokens, (list, tuple)) and tokens and isinstance(
        tokens[0], (list, tuple)
    ):
        groups = [list(g) for g in tokens if isinstance(g, (list, tuple)) and g]
    elif tokens:
        groups = [list(tokens)]
    else:
        groups = []
    weights = params.get("factor_weights")
    if not isinstance(weights, (list, tuple)) or len(weights) != len(groups):
        weights = [1.0] * len(groups) if groups else []
    return groups, [float(w) for w in weights]


def _latest_position(
    bars: list[dict[str, Any]],
    tokens: list[int] | list[list[int]],
    weights: list[float] | None = None,
) -> float | None:
    """计算最新 bar 的 tanh 仓位意图（单公式或加权组合）；失败返回 None"""
    if len(bars) < MIN_BARS:
        return None
    if isinstance(tokens, (list, tuple)) and tokens and isinstance(
        tokens[0], (list, tuple)
    ):
        groups = [list(g) for g in tokens if g]
        w = list(weights or [1.0] * len(groups))
        if len(w) != len(groups):
            w = [1.0] * len(groups)
    elif tokens:
        groups, w = [list(tokens)], [1.0]
    else:
        return None
    if not groups:
        return None
    mat = feature_matrix(bars)
    pos_sum = 0.0
    w_sum = 0.0
    for g, wi in zip(groups, w):
        factor = execute(g, mat)
        if factor is None or is_constant(factor):
            continue  # 无效成员跳过，剩余成员权重重归一
        pos_sum += wi * float(np.tanh(np.clip(float(factor[-1]), -3.0, 3.0)))
        w_sum += wi
    if w_sum <= 0:
        return None
    return pos_sum / w_sum


def compute_factor_signal(
    bars: list[dict[str, Any]],
    params: dict[str, Any],
    side_mode: str,
    position: dict[str, Any] | None,
    exit_rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """根据因子公式（单公式或组合）最新值生成离散动作

    exit_rules: close_rules.factor_exit（因子阈值平仓配置）。
    平仓本身由 engine 的出场检查块执行；此处仅做 reach 模式的入场抑制
    （因子已达离场阈值时不追新仓，防止开完立刻平的抖动）。
    """
    p = normalize_factor_params(params)
    groups, _ = _split_groups(p)
    if not groups:
        return _hold("因子公式为空", "no_factor", p)
    pos = _latest_position(bars, p["factor_tokens"], p.get("factor_weights"))
    if pos is None:
        return _hold("数据不足或因子无效", "invalid_factor", p)

    pos_qty = int(
        (position or {}).get("available_quantity")
        or (position or {}).get("quantity")
        or 0
    )
    pos_dir = str((position or {}).get("direction") or "")
    mode = (side_mode or "both").strip().lower()
    label = f"组合({len(groups)})" if len(groups) > 1 else "因子"
    snap = {"factor_position": round(pos, 3), "factor_text": p["factor_text"]}
    conf = float(min(1.0, abs(pos)))

    entry = float(p.get("entry_threshold") or ENTRY_THRESHOLD)

    # 偏多
    if pos > entry and mode != "short_only":
        if pos_qty > 0 and pos_dir == "long":
            return _result("hold", f"{label}偏多({pos:.2f})已持多", "factor_hold_long", conf, snap, p)
        if pos_qty > 0 and pos_dir == "short":
            return _result("close", f"{label}转多({pos:.2f})平空", "factor_close_short", conf, snap, p)
        if _entry_suppressed(exit_rules, pos):
            return _hold(f"{label}已达离场阈值({pos:.2f})不追多", "factor_exit_suppressed", p, snap)
        return _result("open_long", f"{label}偏多({pos:.2f})开多", "factor_open_long", conf, snap, p)

    # 偏空
    if pos < -entry and mode != "long_only":
        if pos_qty > 0 and pos_dir == "short":
            return _result("hold", f"{label}偏空({pos:.2f})已持空", "factor_hold_short", conf, snap, p)
        if pos_qty > 0 and pos_dir == "long":
            return _result("close", f"{label}转空({pos:.2f})平多", "factor_close_long", conf, snap, p)
        if _entry_suppressed(exit_rules, pos):
            return _hold(f"{label}已达离场阈值({pos:.2f})不追空", "factor_exit_suppressed", p, snap)
        return _result("open_short", f"{label}偏空({pos:.2f})开空", "factor_open_short", conf, snap, p)

    # 弱信号：有仓且接近 0 → 平
    if abs(pos) < NEUTRAL and pos_qty > 0:
        return _result("close", f"{label}中性({pos:.2f})平仓", "factor_neutral_close", conf, snap, p)
    return _hold(f"{label}观望({pos:.2f})", "factor_wait", p, snap)


def factor_snapshot(
    tokens: list[int] | list[list[int]],
    bars: list[dict[str, Any]],
    weights: list[float] | None = None,
) -> dict[str, Any] | None:
    """供 LLM 注入的因子快照：{position, text}；单公式或组合；无效返回 None"""
    if not tokens or len(bars) < MIN_BARS:
        return None
    if isinstance(tokens, (list, tuple)) and tokens and isinstance(
        tokens[0], (list, tuple)
    ):
        text = " ⊕ ".join(to_text(list(g)) for g in tokens if g)
    else:
        text = to_text(list(tokens))
    pos = _latest_position(bars, tokens, weights)
    if pos is None:
        return None
    return {"position": round(pos, 3), "text": text}


def _hold(
    reason: str,
    signal: str,
    params: dict[str, Any],
    snap: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _result("hold", reason, signal, 0.0, snap, params)


def _entry_suppressed(
    exit_rules: dict[str, Any] | None,
    pos: float,
) -> bool:
    """reach 模式下因子已达离场阈值 → 抑制新开仓（懒导入避免与 core.exit_signals 循环依赖）"""
    if not exit_rules:
        return False
    from app.services.ai_trading.core.exit_signals import factor_entry_suppressed

    return factor_entry_suppressed(exit_rules, pos)


def _result(
    action: str,
    reason: str,
    signal: str,
    confidence: float,
    snap: dict[str, Any] | None,
    params: dict[str, Any],
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "action": action,
        "quantity": 0,
        "reason": reason,
        "confidence": confidence,
        "signal": signal,
        "parse_ok": True,
    }
    if snap is not None:
        out["factor_snapshot"] = snap
    return out
