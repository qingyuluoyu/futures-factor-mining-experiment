"""决策对用户展示文案 —— 禁止夹带模型杂文/调试话术"""

from __future__ import annotations

from typing import Any
from uuid import UUID


def public_decision_reason(
    filtered: dict[str, Any],
    model_out: dict[str, Any],
    order_id: UUID | None,
    exec_msg: str,
) -> str:
    """组装对用户展示的简短原因"""
    action = str(filtered.get("action") or "hold")
    model_reason = str(model_out.get("reason") or "").strip()
    filter_reason = str(filtered.get("reason") or "").strip()
    parse_ok = bool(model_out.get("parse_ok", True))

    if not parse_ok and model_reason.startswith(
        ("模型未返回", "无法解析", "模型无输出")
    ):
        return "模型未返回合法JSON，已按观望处理"

    if order_id is not None:
        base = filter_reason or model_reason or action
        if exec_msg and exec_msg != "无需下单":
            return f"{base}（{exec_msg}）"
        return base

    if action == "hold":
        # 若 filter_ai_action 把开仓/平仓改成了 hold（如保证金不足、置信度不足、
        # 未开启 AI 自主平仓等），filter_reason 是真实拦截原因，优先展示给用户；
        # 否则才回退到策略/模型的原始 reason。
        _GENERIC = ("观望", "无需下单", "")
        if filter_reason and filter_reason not in _GENERIC:
            return filter_reason
        if model_reason and not model_reason.startswith("无法解析"):
            return model_reason
        return "观望"

    if exec_msg and "失败" in exec_msg:
        return f"{filter_reason or model_reason or action}；{exec_msg}"
    return filter_reason or model_reason or action
