"""write_run_log 调用形态回归测试

PR #87 曾把 task 定义为 keyword-only，而 engine.py（硬规则/信号两处）与
loop.py（异常兜底）均按 write_run_log(session, task, ...) 位置调用——
TypeError 在参数绑定时抛出，逃出 best-effort 保护，导致：
- run-once 接口 500（「AI任务分析失败」）
- 循环评估失败且 last_bar_time 不更新 → 同一 bar 重复评估（重复下单风险）
- 异常兜底自身的错误日志也写不进去

本测试用与调用方完全一致的形态直接调用真实函数，锁死该回归。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services.ai_trading.task.run_log_svc import write_run_log


def _task() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        last_bar_time=None,
        timeframe="5m",
        symbol="rb2610",
        strategy_type="ai",
        side_mode="both",
    )


@pytest.mark.asyncio
async def test_positional_task_call_form_does_not_raise() -> None:
    """engine/loop 的调用形态：session、task 按位置，其余关键字"""
    session = AsyncMock()
    session.add = MagicMock()
    task = _task()

    # 曾在参数绑定阶段抛 TypeError: takes 1 positional argument but 2 ...
    await write_run_log(
        session, task,
        level="error",
        event="error",
        reason="评估异常: TypeError: boom",
        detail={"error_type": "TypeError"},
    )
    session.add.assert_called_once()
    session.flush.assert_awaited()


@pytest.mark.asyncio
async def test_hard_rule_call_form_with_all_kwargs() -> None:
    """engine 硬规则/信号路径形态：含 action/order_id/detail/bar_time 全参"""
    session = AsyncMock()
    session.add = MagicMock()
    task = _task()
    order_id = uuid4()

    await write_run_log(
        session, task,
        level="warn",
        event="hard_rule",
        action="close",
        order_id=order_id,
        reason="止损触发",
        detail={"last_price": 3400.0},
        bar_time="2026-08-17 13:30:00",
    )
    row = session.add.call_args[0][0]
    assert str(row.task_id) == str(task.id)
    assert row.level == "warn"
    assert row.event == "hard_rule"
    assert row.order_id == order_id


@pytest.mark.asyncio
async def test_keyword_task_still_supported() -> None:
    """task=task 关键字形态（未来调用方）同样可用"""
    session = AsyncMock()
    session.add = MagicMock()
    await write_run_log(session, task=_task(), event="signal")
    session.add.assert_called_once()


@pytest.mark.asyncio
async def test_list_run_logs_returns_rows_total_tuple() -> None:
    """list_run_logs 返回 (rows, total) 元组——API 端点按此解包组装 dict。

    回归：端点曾直接 return 该元组，与 -> dict 响应模型冲突导致 500
    （前端表现为「运行日志」Tab 永远为空）。
    """
    from datetime import datetime, timezone
    from app.services.ai_trading.task.run_log_svc import list_run_logs

    def _log_row() -> SimpleNamespace:
        return SimpleNamespace(
            id=uuid4(),
            task_id=uuid4(),
            user_id=uuid4(),
            bar_time=None,
            timeframe="15m",
            symbol="p2609",
            strategy_type="ai",
            side_mode="both",
            level="info",
            event="signal",
            action="hold",
            order_id=None,
            reason="观望",
            detail=None,
            created_at=datetime.now(timezone.utc),
        )

    session = AsyncMock()
    result = MagicMock()
    result.scalar.return_value = 7  # count
    executed = MagicMock()
    executed.scalars.return_value.all.return_value = [_log_row() for _ in range(3)]
    session.execute.side_effect = [result, executed]

    out = await list_run_logs(session, task_id=uuid4(), limit=50, offset=0)
    assert isinstance(out, tuple) and len(out) == 2
    rows_out, total = out
    assert isinstance(rows_out, list) and len(rows_out) == 3
    assert "task_id" in rows_out[0]
    assert total == 7
