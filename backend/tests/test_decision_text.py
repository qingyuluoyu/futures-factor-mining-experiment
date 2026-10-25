"""public_decision_reason 文案优先级单测

重点：当 filter_ai_action 把开仓/平仓改成 hold 时（保证金不足、置信度
不足等），应对用户显示 filter_reason（真实拦截原因），而非策略/模型的
原始 reason（如"因子偏空开空"，会误导用户以为要开仓）。
"""

from __future__ import annotations

from uuid import uuid4

from app.services.ai_trading.decision_text import public_decision_reason


def _call(
    *,
    filtered_action: str,
    filtered_reason: str = "",
    model_reason: str = "",
    order_id: object = None,
    exec_msg: str = "",
) -> str:
    return public_decision_reason(
        filtered={"action": filtered_action, "reason": filtered_reason},
        model_out={"reason": model_reason, "parse_ok": True},
        order_id=order_id,
        exec_msg=exec_msg,
    )


class TestHoldShowsFilterReasonFirst:
    """action=hold 时，若 filter 有实质拦截原因，必须优先显示"""

    def test_margin_block_overrides_strategy_reason(self) -> None:
        """保证金不足拦截：应显示"保证金不足"，而非策略的"因子偏空开空" """
        reason = _call(
            filtered_action="hold",
            filtered_reason="保证金不足，无法满足最少 10 手",
            model_reason="因子偏空(-0.84)开空",
        )
        assert "保证金不足" in reason
        assert "因子偏空" not in reason

    def test_confidence_block_overrides_model_reason(self) -> None:
        """置信度不足拦截：应显示置信度原因"""
        reason = _call(
            filtered_action="hold",
            filtered_reason="置信度 0.20 低于aggressive风格门槛 0.30，观望",
            model_reason="建议开多",
        )
        assert "置信度" in reason
        assert "建议开多" not in reason

    def test_close_blocked_shows_filter_reason(self) -> None:
        """未开启 AI 自主平仓导致 close 被改成 hold"""
        reason = _call(
            filtered_action="hold",
            filtered_reason="未开启 AI 自主平仓/止损",
            model_reason="趋势反转，建议平仓",
        )
        assert "未开启" in reason
        assert "趋势反转" not in reason


class TestHoldFallsBackToModelReason:
    """action=hold 且 filter_reason 泛泛时，回退到策略/模型 reason"""

    def test_generic_filter_falls_back_to_model(self) -> None:
        reason = _call(
            filtered_action="hold",
            filtered_reason="观望",
            model_reason="因子观望(-0.10)",
        )
        assert "因子观望" in reason

    def test_empty_filter_falls_back_to_model(self) -> None:
        reason = _call(
            filtered_action="hold",
            filtered_reason="",
            model_reason="数据不足",
        )
        assert "数据不足" in reason

    def test_no_reason_at_all(self) -> None:
        reason = _call(filtered_action="hold", filtered_reason="", model_reason="")
        assert reason == "观望"


class TestNonHoldActions:
    """非 hold 动作保持原行为"""

    def test_open_with_order_shows_filter_reason(self) -> None:
        reason = _call(
            filtered_action="open_long",
            filtered_reason="AI 开多",
            model_reason="看多",
            order_id=uuid4(),
            exec_msg="无需下单",
        )
        assert "AI 开多" in reason
