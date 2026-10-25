"""AI 任务胜率统计测试

验证每次平仓后 _apply_win_stats 按 realized_pnl 正负正确更新
win_count / loss_count / trade_count / win_rate，并覆盖三种路径
（盈利/亏损/平局）。
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from app.services.ai_trading.engine import _apply_win_stats


def _task(win: int = 0, loss: int = 0) -> SimpleNamespace:
    """构造带胜率字段的轻量 task 对象"""
    total = win + loss
    rate = Decimal(str(round(win / total * 100, 2))) if total > 0 else Decimal("0")
    return SimpleNamespace(
        win_count=win,
        loss_count=loss,
        trade_count=total,
        win_rate=rate,
    )


def _session_with_pnl(pnl: Decimal | None) -> AsyncMock:
    """构造返回指定 realized_pnl 的 mock session。

    session.execute 是 async（返回 Result 对象），而 Result.scalar_one_or_none
    是同步方法，故 result 用 MagicMock 而非 AsyncMock。
    """
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=pnl)
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.mark.asyncio
async def test_win_stats_profit_increments_win_count():
    """平仓盈利 → win_count +1，win_rate 重算"""
    task = _task(win=1, loss=1)  # 初始 1胜1负，胜率50%
    session = _session_with_pnl(Decimal("500"))
    await _apply_win_stats(session, task, UUID(uuid4().hex))
    assert task.win_count == 2
    assert task.loss_count == 1
    assert task.trade_count == 3
    assert task.win_rate == Decimal("66.67")  # 2/3*100


@pytest.mark.asyncio
async def test_win_stats_loss_increments_loss_count():
    """平仓亏损 → loss_count +1"""
    task = _task(win=2, loss=0)
    session = _session_with_pnl(Decimal("-300"))
    await _apply_win_stats(session, task, UUID(uuid4().hex))
    assert task.win_count == 2
    assert task.loss_count == 1
    assert task.trade_count == 3
    assert task.win_rate == Decimal("66.67")


@pytest.mark.asyncio
async def test_win_stats_zero_pnl_counts_as_loss():
    """realized_pnl == 0 计为亏损（<= 0 分支）"""
    task = _task(win=1, loss=0)
    session = _session_with_pnl(Decimal("0"))
    await _apply_win_stats(session, task, UUID(uuid4().hex))
    assert task.win_count == 1
    assert task.loss_count == 1
    assert task.trade_count == 2
    assert task.win_rate == Decimal("50.00")


@pytest.mark.asyncio
async def test_win_stats_no_order_id_is_noop():
    """order_id 为空时不更新（未成交）"""
    task = _task(win=3, loss=2)
    session = _session_with_pnl(Decimal("100"))
    await _apply_win_stats(session, task, None)
    assert task.win_count == 3
    assert task.loss_count == 2
    assert task.trade_count == 5


@pytest.mark.asyncio
async def test_win_stats_order_not_found_is_noop():
    """order_id 有值但查不到记录（scalar 返回 None）时不更新"""
    task = _task(win=1, loss=1)
    session = _session_with_pnl(None)
    await _apply_win_stats(session, task, UUID(uuid4().hex))
    assert task.trade_count == 2  # 未变


@pytest.mark.asyncio
async def test_win_stats_from_zero_state():
    """从 0 胜 0 负开始：首笔盈利 → 胜率 100%"""
    task = _task(win=0, loss=0)
    session = _session_with_pnl(Decimal("50"))
    await _apply_win_stats(session, task, UUID(uuid4().hex))
    assert task.win_count == 1
    assert task.loss_count == 0
    assert task.trade_count == 1
    assert task.win_rate == Decimal("100.00")
