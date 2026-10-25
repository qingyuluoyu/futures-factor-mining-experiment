"""AI 交易硬风控 —— 止损/平仓/多空/仓位，优先于模型输出

仓位模式：
- fixed_qty: 指定手数，目标仓位=fixed_qty，已有仓则不再加仓
- half / full: 按可用保证金一半/全部开首仓，已有仓则不再加仓
- scale_in(滚仓): 以 fixed_qty 为单位；浮盈时允许同向加仓，最多 MAX_SCALE_LAYERS 层
"""

from __future__ import annotations

from typing import Any

# 滚仓最大层数（含首仓）：如 fixed_qty=1 最多持 3 手
MAX_SCALE_LAYERS = 3


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def position_pnl_pct(position: dict[str, Any] | None, last_price: float) -> float | None:
    """持仓浮动盈亏百分比（相对开仓价，按方向）"""
    if not position or last_price <= 0:
        return None
    avg = _f(position.get("avg_price"))
    if avg <= 0:
        return None
    direction = str(position.get("direction") or "")
    if direction == "long":
        return (last_price - avg) / avg * 100.0
    if direction == "short":
        return (avg - last_price) / avg * 100.0
    return None


def position_pnl_amount(
    position: dict[str, Any] | None,
    last_price: float,
) -> float | None:
    """持仓浮动盈亏金额（元）≈ 价差 * 乘数 * 手数"""
    if not position or last_price <= 0:
        return None
    avg = _f(position.get("avg_price"))
    qty = int(position.get("quantity") or 0)
    mult = int(position.get("multiplier") or 10)
    if avg <= 0 or qty <= 0:
        return None
    direction = str(position.get("direction") or "")
    if direction == "long":
        return (last_price - avg) * mult * qty
    if direction == "short":
        return (avg - last_price) * mult * qty
    return None


def check_hard_rules(
    *,
    position: dict[str, Any] | None,
    last_price: float,
    stop_rules: dict[str, Any],
    close_rules: dict[str, Any],
    cash_delta_pct: float | None,
    session_closing: bool,
    risk_style: str | None = None,
) -> dict[str, Any] | None:
    """硬规则检查；命中则返回强制动作 dict，否则 None

    stop_rules / close_rules 应为「风格缩放后」的生效规则；
    若仍含 _user_* 基准字段，reason 中会附带用户原值便于审计。
    """
    qty = int(
        (position or {}).get("available_quantity")
        or (position or {}).get("quantity")
        or 0
    )
    has_pos = position is not None and qty > 0
    pnl_pct = position_pnl_pct(position, last_price) if has_pos else None
    pnl_amt = position_pnl_amount(position, last_price) if has_pos else None
    style_tag = ""
    if risk_style:
        style_tag = f"[{risk_style}]"

    if has_pos:
        loss_pct = stop_rules.get("loss_pct")
        if loss_pct is not None and pnl_pct is not None and pnl_pct <= -abs(_f(loss_pct)):
            user_ref = stop_rules.get("_user_loss_pct")
            extra = (
                f"（用户止损 {abs(_f(user_ref)):.2f}%→风格生效 {abs(_f(loss_pct)):.2f}%）"
                if user_ref is not None
                else ""
            )
            return {
                "action": "close",
                "reason": (
                    f"止损{style_tag}：浮亏 {pnl_pct:.2f}% ≤ -{abs(_f(loss_pct)):.2f}%"
                    f"{extra}"
                ),
                "trigger_type": "stop",
                "quantity": qty,
            }
        loss_amount = stop_rules.get("loss_amount")
        if (
            loss_amount is not None
            and pnl_amt is not None
            and pnl_amt <= -abs(_f(loss_amount))
        ):
            user_ref = stop_rules.get("_user_loss_amount")
            extra = (
                f"（用户 {abs(_f(user_ref)):.2f}→风格 {abs(_f(loss_amount)):.2f}）"
                if user_ref is not None
                else ""
            )
            return {
                "action": "close",
                "reason": (
                    f"止损{style_tag}：浮亏金额 {pnl_amt:.2f} ≤ -{abs(_f(loss_amount)):.2f}"
                    f"{extra}"
                ),
                "trigger_type": "stop",
                "quantity": qty,
            }

    if has_pos:
        target_pct = close_rules.get("pnl_pct")
        if (
            target_pct is not None
            and pnl_pct is not None
            and pnl_pct >= abs(_f(target_pct))
        ):
            user_ref = close_rules.get("_user_pnl_pct")
            extra = (
                f"（用户止盈 {abs(_f(user_ref)):.2f}%→风格生效 {abs(_f(target_pct)):.2f}%）"
                if user_ref is not None
                else ""
            )
            return {
                "action": "close",
                "reason": (
                    f"止盈{style_tag}：浮盈 {pnl_pct:.2f}% ≥ {abs(_f(target_pct)):.2f}%"
                    f"{extra}"
                ),
                "trigger_type": "close_rule",
                "quantity": qty,
            }
        if close_rules.get("session_close") and session_closing:
            return {
                "action": "close",
                "reason": "收盘平仓规则触发",
                "trigger_type": "session_close",
                "quantity": qty,
            }

    total_target = close_rules.get("total_pnl_pct")
    if (
        has_pos
        and total_target is not None
        and cash_delta_pct is not None
        and cash_delta_pct >= abs(_f(total_target))
    ):
        user_ref = close_rules.get("_user_total_pnl_pct")
        extra = (
            f"（用户 {abs(_f(user_ref)):.2f}%→风格 {abs(_f(total_target)):.2f}%）"
            if user_ref is not None
            else ""
        )
        return {
            "action": "close",
            "reason": (
                f"总收益止盈{style_tag}：相对 baseline {cash_delta_pct:.2f}% "
                f"≥ {abs(_f(total_target)):.2f}%{extra}"
            ),
            "trigger_type": "close_rule",
            "quantity": qty,
        }

    return None


def filter_ai_action(
    *,
    action: str,
    quantity: int,
    side_mode: str,
    position_mode: str,
    fixed_qty: int,
    position: dict[str, Any] | None,
    available_margin: float,
    last_price: float,
    multiplier: int,
    margin_rate: float,
    close_ai_auto: bool,
    stop_ai_auto: bool,
    risk_style: str = "balanced",
    confidence: float = 1.0,
    min_confidence: float | None = None,
    custom_prompt_enabled: bool = False,
    qty_min: int | None = None,
    qty_max: int | None = None,
) -> dict[str, Any]:
    """将 AI 动作约束到多空/仓位规则内，返回规范化决策"""
    act = (action or "hold").strip().lower()
    qty = max(0, int(quantity or 0))
    pos_qty = int(
        (position or {}).get("available_quantity")
        or (position or {}).get("quantity")
        or 0
    )
    pos_dir = str((position or {}).get("direction") or "")
    mode = (position_mode or "fixed_qty").strip().lower()
    base_lot = max(1, int(fixed_qty or 1))
    # 手数范围（缺省 = fixed_qty）
    q_lo = max(1, int(qty_min if qty_min is not None else base_lot))
    q_hi = max(1, int(qty_max if qty_max is not None else base_lot))
    if q_hi < q_lo:
        q_lo, q_hi = q_hi, q_lo

    # 风格置信度门槛：仅约束开仓；启用用户提示词时风格门槛失效
    from app.services.ai_trading.core.capital import get_style_profile

    style = (risk_style or "balanced").strip().lower()
    profile = get_style_profile(style, custom_prompt_enabled)
    conf_floor = (
        float(min_confidence)
        if min_confidence is not None
        else float(profile.get("min_confidence") or 0.0)
    )
    conf = 0.0
    try:
        conf = float(confidence)
    except (TypeError, ValueError):
        conf = 0.0

    if act in ("close", "close_long", "close_short"):
        if not close_ai_auto and not stop_ai_auto:
            return {"action": "hold", "quantity": 0, "reason": "未开启 AI 自主平仓/止损"}
        if pos_qty <= 0:
            return {"action": "hold", "quantity": 0, "reason": "无持仓可平"}
        return {
            "action": "close",
            "quantity": pos_qty if qty <= 0 else min(qty, pos_qty),
            "reason": "AI 平仓",
        }

    if act in ("open_long", "buy", "long", "open_short", "sell", "short"):
        if conf < conf_floor:
            return {
                "action": "hold",
                "quantity": 0,
                "reason": (
                    f"置信度 {conf:.2f} 低于{style}风格门槛 {conf_floor:.2f}，观望"
                ),
            }

    if act in ("open_long", "buy", "long"):
        resolved = _resolve_open(
            want_dir="long",
            open_action="open_long",
            side_mode=side_mode,
            mode=mode,
            base_lot=base_lot,
            ai_qty=qty,
            pos_qty=pos_qty,
            pos_dir=pos_dir,
            position=position,
            available_margin=available_margin,
            last_price=last_price,
            multiplier=multiplier,
            margin_rate=margin_rate,
            qty_min=q_lo,
            qty_max=q_hi,
        )
        return _clamp_open_qty(resolved, q_lo, q_hi, available_margin, last_price, multiplier, margin_rate)

    if act in ("open_short", "sell", "short"):
        resolved = _resolve_open(
            want_dir="short",
            open_action="open_short",
            side_mode=side_mode,
            mode=mode,
            base_lot=base_lot,
            ai_qty=qty,
            pos_qty=pos_qty,
            pos_dir=pos_dir,
            position=position,
            available_margin=available_margin,
            last_price=last_price,
            multiplier=multiplier,
            margin_rate=margin_rate,
            qty_min=q_lo,
            qty_max=q_hi,
        )
        return _clamp_open_qty(resolved, q_lo, q_hi, available_margin, last_price, multiplier, margin_rate)

    return {"action": "hold", "quantity": 0, "reason": "观望"}


def _clamp_open_qty(
    resolved: dict[str, Any],
    qty_min: int,
    qty_max: int,
    available_margin: float,
    last_price: float,
    multiplier: int,
    margin_rate: float,
) -> dict[str, Any]:
    """将开仓手数夹到 [qty_min, qty_max] 且不超过保证金"""
    act = str(resolved.get("action") or "hold")
    if act not in ("open_long", "open_short"):
        return resolved
    use = int(resolved.get("quantity") or 0)
    if use <= 0:
        return resolved
    margin_cap = _margin_lots(available_margin, last_price, multiplier, margin_rate)
    # 先夹到用户手数范围
    use = max(qty_min, min(qty_max, use))
    # 保证金不足时降到可开手数，但不得低于 qty_min（否则 hold）
    if margin_cap < qty_min:
        return {
            "action": "hold",
            "quantity": 0,
            "reason": f"保证金不足，无法满足最少 {qty_min} 手",
        }
    use = min(use, margin_cap, qty_max)
    if use < qty_min:
        return {
            "action": "hold",
            "quantity": 0,
            "reason": f"可开手数 {use} 低于最少 {qty_min} 手",
        }
    reason = str(resolved.get("reason") or "")
    if f"{qty_min}-{qty_max}" not in reason:
        reason = f"{reason}（手数范围 {qty_min}-{qty_max}）".strip("（）")
        if reason.startswith("（"):
            reason = f"手数范围 {qty_min}-{qty_max}"
    return {"action": act, "quantity": use, "reason": reason}


def _resolve_open(
    *,
    want_dir: str,
    open_action: str,
    side_mode: str,
    mode: str,
    base_lot: int,
    ai_qty: int,
    pos_qty: int,
    pos_dir: str,
    position: dict[str, Any] | None,
    available_margin: float,
    last_price: float,
    multiplier: int,
    margin_rate: float,
    qty_min: int = 1,
    qty_max: int = 1,
) -> dict[str, Any]:
    """开仓/加仓统一解析"""
    if want_dir == "long" and side_mode == "short_only":
        return {"action": "hold", "quantity": 0, "reason": "仅做空模式禁止开多"}
    if want_dir == "short" and side_mode == "long_only":
        return {"action": "hold", "quantity": 0, "reason": "仅做多模式禁止开空"}

    # 反向持仓：必须先平，禁止直接反手
    if pos_qty > 0 and pos_dir and pos_dir != want_dir:
        return {
            "action": "hold",
            "quantity": 0,
            "reason": f"已有{'多' if pos_dir == 'long' else '空'}仓，请先平仓再开{'多' if want_dir == 'long' else '空'}",
        }

    margin_cap = _margin_lots(
        available_margin, last_price, multiplier, margin_rate
    )
    lot_lo = max(1, int(qty_min or base_lot))
    lot_hi = max(lot_lo, int(qty_max or base_lot))

    # —— 滚仓：同向 + 浮盈 才允许加仓 ——
    if mode == "scale_in":
        # 总持仓上限 = 每层最大手数 × 层数
        max_total = lot_hi * MAX_SCALE_LAYERS
        if pos_qty <= 0:
            # 首仓：AI 建议夹到 [lot_lo, lot_hi]
            prefer = ai_qty if ai_qty > 0 else lot_lo
            use = min(max(lot_lo, prefer), lot_hi, margin_cap) if margin_cap > 0 else 0
            if use < lot_lo:
                return {"action": "hold", "quantity": 0, "reason": "可用保证金不足，无法开首仓"}
            return {
                "action": open_action,
                "quantity": use,
                "reason": f"滚仓首仓 {use} 手",
            }

        # 已有同向仓
        if pos_qty >= max_total:
            return {
                "action": "hold",
                "quantity": 0,
                "reason": f"已达滚仓上限 {max_total} 手（{MAX_SCALE_LAYERS} 层）",
            }
        pnl = position_pnl_amount(position, last_price)
        if pnl is None or pnl <= 0:
            return {
                "action": "hold",
                "quantity": 0,
                "reason": "滚仓仅在浮盈时加仓，当前未盈利",
            }
        room = max_total - pos_qty
        prefer = ai_qty if ai_qty > 0 else lot_lo
        use = min(max(lot_lo, prefer), lot_hi, room, margin_cap)
        if use < lot_lo:
            return {"action": "hold", "quantity": 0, "reason": "保证金不足，无法继续滚仓"}
        return {
            "action": open_action,
            "quantity": use,
            "reason": f"滚仓加仓 {use} 手（浮盈 {pnl:.0f}）",
        }

    # —— 非滚仓：已有仓则不再加仓（只允许平/观望）——
    if pos_qty > 0 and pos_dir == want_dir:
        return {
            "action": "hold",
            "quantity": 0,
            "reason": "已有持仓，非滚仓模式不加仓",
        }

    cap = _position_cap(
        mode,
        base_lot,
        available_margin,
        last_price,
        multiplier,
        margin_rate,
    )
    # fixed_qty：目标在手数范围内；half/full：按保证金再夹范围
    if mode == "fixed_qty":
        prefer = ai_qty if ai_qty > 0 else lot_lo
        use = min(max(lot_lo, prefer), lot_hi, max(cap, lot_hi))
        # 指定手数仍受保证金约束
        use = min(use, margin_cap) if margin_cap > 0 else 0
    else:
        use = cap if ai_qty <= 0 else min(ai_qty, cap)
        use = min(max(use, 0), lot_hi)
        if 0 < use < lot_lo:
            # 半仓/全仓算出低于最少手数则尝试开最少手
            use = lot_lo if margin_cap >= lot_lo else 0
    if use <= 0:
        return {"action": "hold", "quantity": 0, "reason": "可用保证金不足或手数为 0"}
    label = {"full": "全仓", "half": "半仓", "fixed_qty": "指定手数"}.get(mode, mode)
    return {
        "action": open_action,
        "quantity": use,
        "reason": f"AI 开{'多' if want_dir == 'long' else '空'}（{label} {use} 手）",
    }


def _margin_lots(
    available_margin: float,
    last_price: float,
    multiplier: int,
    margin_rate: float,
) -> int:
    if last_price <= 0 or multiplier <= 0 or margin_rate <= 0:
        return 0
    margin_per_lot = last_price * multiplier * margin_rate
    if margin_per_lot <= 0:
        return 0
    return max(0, int(available_margin // margin_per_lot))


def _position_cap(
    position_mode: str,
    fixed_qty: int,
    available_margin: float,
    last_price: float,
    multiplier: int,
    margin_rate: float,
) -> int:
    """按仓位模式计算最大可开手数（首仓）"""
    if position_mode == "fixed_qty":
        return max(1, int(fixed_qty))
    if position_mode == "scale_in":
        return max(1, int(fixed_qty))
    max_by_margin = _margin_lots(
        available_margin, last_price, multiplier, margin_rate
    )
    if position_mode == "half":
        return max(0, max_by_margin // 2)
    # full
    return max(0, max_by_margin)
