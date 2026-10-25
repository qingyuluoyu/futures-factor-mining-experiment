"""evaluate_task skip 路径清理过期「评估异常」note 的回归测试

历史问题：execute.py 的 available_quantity 崩溃修复后，曾经报错的任务（尤其
日线/低频 timeframe 任务）在两次 bar 之间长期走 no_new_bar / market_closed
skip 路径，而这些路径不清 task.note，导致修复后 note 仍挂着
「最近一次评估异常，将自动重试」误导用户。

修复：no_new_bar / market_closed / hard 规则等「评估健康但本次无需动作」的
路径都应清掉过期异常 note。
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.models.ai_trading.task import AITradingTask
from app.services.ai_trading.engine import evaluate_task


def _make_task(*, strategy_type: str = "factor", timeframe: str = "1d") -> AITradingTask:
    """构造一个带「评估异常」note 的 running 任务"""
    return AITradingTask(
        id=uuid4(),
        user_id=uuid4(),
        trading_mode="live",
        name="橡胶因子算法",
        model_row_id=None,
        strategy_type=strategy_type,
        strategy_params={},
        icon=None,
        symbol="ru2609",
        symbol_name="橡胶2609",
        timeframe=timeframe,
        side_mode="both",
        position_mode="single",
        fixed_qty=1,
        qty_min=1,
        qty_max=1,
        capital_usage_min_pct=Decimal("0"),
        capital_usage_max_pct=Decimal("100"),
        allocated_capital=Decimal("0"),
        capital_balance=Decimal("0"),
        risk_style="balanced",
        max_hold_days=10,
        custom_prompt_enabled=False,
        custom_prompt=None,
        position_opened_at=None,
        close_rules={},
        stop_rules={},
        status="running",
        pause_reason=None,
        last_bar_time="2026-07-28",
        note="最近一次评估异常，将自动重试",  # 模拟崩溃修复后残留的过期 note
    )


@pytest.mark.asyncio
async def test_no_new_bar_skip_clears_stale_error_note() -> None:
    """no_new_bar skip 路径应清掉过期的「评估异常」note

    复现：日线任务在两次 bar 之间走 no_new_bar，修复前不清 note。
    """
    task = _make_task(timeframe="1d")
    session = AsyncMock()
    session.flush = AsyncMock()

    with (
        patch("app.services.ai_trading.engine.get_session_status") as m_sess,
        patch("app.services.ai_trading.engine.get_kline_history", new=AsyncMock()) as m_kline,
        patch("app.services.ai_trading.engine.resolve_last_price", new=AsyncMock(return_value=15000.0)),
        patch("app.services.ai_trading.engine.find_symbol_position", new=AsyncMock(return_value=None)),
        patch("app.services.ai_trading.engine.get_account_summary", new=AsyncMock(return_value={"total_equity": 100000})),
        patch("app.services.ai_trading.engine.check_hard_rules", return_value=None),
        patch("app.services.ai_trading.engine.should_run_for_bar", return_value=(False, None)),
        patch("app.services.ai_trading.engine.snapshot_equity", new=AsyncMock()),
    ):
        m_sess.return_value = {"is_open": True}
        m_kline.return_value = [{"time": "2026-07-28", "close": 15000}]

        result = await evaluate_task(session, task, redis_conn=None, force=False)

    # 走了 no_new_bar skip
    assert result == {"skipped": True, "reason": "no_new_bar"}
    # 关键断言：过期异常 note 被清空
    assert task.note is None


@pytest.mark.asyncio
async def test_market_closed_skip_clears_stale_error_note() -> None:
    """market_closed skip 路径也应清掉过期异常 note"""
    task = _make_task(timeframe="1m")
    session = AsyncMock()
    session.flush = AsyncMock()

    with (
        patch("app.services.ai_trading.engine.get_session_status") as m_sess,
        patch("app.services.ai_trading.engine.get_kline_history", new=AsyncMock()) as m_kline,
        patch("app.services.ai_trading.engine.resolve_last_price", new=AsyncMock(return_value=15000.0)),
        patch("app.services.ai_trading.engine.find_symbol_position", new=AsyncMock(return_value=None)),
        patch("app.services.ai_trading.engine.get_account_summary", new=AsyncMock(return_value={"total_equity": 100000})),
        patch("app.services.ai_trading.engine.check_hard_rules", return_value=None),
        # 有新 bar 但市场休市 → market_closed
        patch("app.services.ai_trading.engine.should_run_for_bar", return_value=(True, {"time": "2026-07-29", "close": 15000})),
        patch("app.services.ai_trading.engine.snapshot_equity", new=AsyncMock()),
    ):
        m_sess.return_value = {"is_open": False}
        m_kline.return_value = [{"time": "2026-07-29", "close": 15000}]

        result = await evaluate_task(session, task, redis_conn=None, force=False)

    assert result == {"skipped": True, "reason": "market_closed"}
    assert task.note is None
