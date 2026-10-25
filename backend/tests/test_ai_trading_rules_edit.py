"""update_task_rules / can_edit_rules 单测 —— 无持仓即可调整盈亏比例

- running + 无持仓：规则更新、状态不变（不回落 paused，运行中下轮评估生效）
- 任意状态 + 有持仓：400 拒绝
- 量化任务：ai_auto 强制 False（与 update_task 同口径）
- _task_trade_flags：can_edit_rules 口径 = 无持仓（与 status 无关）
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.services.ai_trading.task import task_svc


TASK_ID = UUID(int=7)
USER_ID = UUID(int=1)


def _task_stub(**over) -> SimpleNamespace:
    base = dict(
        id=TASK_ID,
        user_id=USER_ID,
        symbol="rb2510",
        strategy_type="ai",
        status="running",
        close_rules={
            "pnl_pct": 5,
            "total_pnl_pct": None,
            "session_close": False,
            "ai_auto": True,
        },
        stop_rules={"loss_pct": 3, "loss_amount": None, "ai_auto": True},
    )
    base.update(over)
    return SimpleNamespace(**base)


def _data(
    task: SimpleNamespace,
    *,
    has_open_position: bool = False,
    has_orders: bool = True,
) -> dict:
    return {
        "id": str(task.id),
        "status": task.status,
        "close_rules": dict(task.close_rules),
        "stop_rules": dict(task.stop_rules),
        "has_open_position": has_open_position,
        "has_orders": has_orders,
        "can_edit": task.status == "stopped"
        and (not has_orders or not has_open_position),
        "can_edit_rules": not has_open_position,
    }


@pytest.mark.asyncio
async def test_rules_updated_while_running(monkeypatch) -> None:
    """running + 无持仓：规则更新，status 保持 running（不回落 paused）"""
    task = _task_stub(status="running")

    async def fake_get_task(session, user_id, task_id, *args, **kwargs):
        return task, _data(task)

    monkeypatch.setattr(task_svc, "get_task", fake_get_task)
    session = AsyncMock()

    out = await task_svc.update_task_rules(
        session,
        USER_ID,
        TASK_ID,
        {
            "close_rules": {
                "pnl_pct": 8,
                "total_pnl_pct": 20,
                "session_close": True,
                "ai_auto": False,
            },
            "stop_rules": {"loss_pct": 2, "loss_amount": 500, "ai_auto": True},
        },
    )

    assert task.close_rules["pnl_pct"] == 8
    assert task.close_rules["total_pnl_pct"] == 20
    assert task.close_rules["session_close"] is True
    assert task.close_rules["ai_auto"] is False
    assert task.stop_rules["loss_pct"] == 2
    assert task.stop_rules["loss_amount"] == 500
    assert task.status == "running"
    assert out["close_rules"]["pnl_pct"] == 8
    session.flush.assert_awaited()


@pytest.mark.asyncio
async def test_rules_rejected_with_open_position(monkeypatch) -> None:
    """该品种仍有持仓：400 拒绝，规则不动"""
    task = _task_stub()

    async def fake_get_task(session, user_id, task_id, *args, **kwargs):
        return task, _data(task, has_open_position=True)

    monkeypatch.setattr(task_svc, "get_task", fake_get_task)

    with pytest.raises(HTTPException) as exc:
        await task_svc.update_task_rules(
            AsyncMock(),
            USER_ID,
            TASK_ID,
            {"close_rules": {"pnl_pct": 9}},
        )
    assert exc.value.status_code == 400
    assert task.close_rules["pnl_pct"] == 5  # 原值未动


@pytest.mark.asyncio
async def test_quant_rules_ai_auto_forced_off(monkeypatch) -> None:
    """量化任务：ai_auto 强制 False（禁止 AI 自主平仓/止损语义）"""
    task = _task_stub(strategy_type="ma_cross")

    async def fake_get_task(session, user_id, task_id, *args, **kwargs):
        return task, _data(task)

    monkeypatch.setattr(task_svc, "get_task", fake_get_task)

    await task_svc.update_task_rules(
        AsyncMock(),
        USER_ID,
        TASK_ID,
        {
            "close_rules": {"pnl_pct": 6, "ai_auto": True},
            "stop_rules": {"loss_pct": 4, "ai_auto": True},
        },
    )
    assert task.close_rules["ai_auto"] is False
    assert task.stop_rules["ai_auto"] is False


@pytest.mark.asyncio
async def test_rules_with_exit_standards_normalized(monkeypatch) -> None:
    """无持仓热改规则：指标/因子平仓扩展字段规范化后落库"""
    task = _task_stub()

    async def fake_get_task(session, user_id, task_id, *args, **kwargs):
        return task, _data(task)

    monkeypatch.setattr(task_svc, "get_task", fake_get_task)

    await task_svc.update_task_rules(
        AsyncMock(),
        USER_ID,
        TASK_ID,
        {
            "close_rules": {
                "pnl_pct": 7,
                "indicator_exits": [
                    {"kind": "macd_cross"},
                    {"kind": "rsi"},  # 白名单外丢弃
                    {"kind": "ma_cross", "params": {"fast_period": 5, "slow_period": 20}},
                ],
                "factor_exit": {"mode": "reach", "long_threshold": 0.5, "short_threshold": -0.9},
            },
        },
    )
    exits = task.close_rules["indicator_exits"]
    assert [e["kind"] for e in exits] == ["macd_cross", "ma_cross"]
    assert exits[0]["params"] == {"fast_period": 12, "slow_period": 26, "signal_period": 9}
    fx = task.close_rules["factor_exit"]
    assert fx["mode"] == "reach"
    assert fx["long_threshold"] == 0.5
    assert fx["short_threshold"] == 0.9  # 负数按绝对值
    assert task.close_rules["pnl_pct"] == 7


@pytest.mark.asyncio
async def test_rules_drop_empty_exit_standards(monkeypatch) -> None:
    """空 indicator_exits / 空 factor_exit：视为未配置，键直接移除"""
    task = _task_stub()

    async def fake_get_task(session, user_id, task_id, *args, **kwargs):
        return task, _data(task)

    monkeypatch.setattr(task_svc, "get_task", fake_get_task)

    await task_svc.update_task_rules(
        AsyncMock(),
        USER_ID,
        TASK_ID,
        {
            "close_rules": {
                "pnl_pct": 6,
                "indicator_exits": [],
                "factor_exit": {},
            },
        },
    )
    assert "indicator_exits" not in task.close_rules
    assert "factor_exit" not in task.close_rules


@pytest.mark.asyncio
async def test_trade_flags_can_edit_rules() -> None:
    """can_edit_rules 只看持仓；can_edit 仍是 stopped+无持仓/未下单"""
    task = _task_stub(status="running")

    # 依次应答两条查询：count(decisions) → scalar_one；PaperPosition → scalar_one_or_none
    no_pos_session = AsyncMock()
    no_pos_session.execute = AsyncMock(
        side_effect=[
            SimpleNamespace(scalar_one=lambda: 3),
            SimpleNamespace(scalar_one_or_none=lambda: None),
        ]
    )
    flags = await task_svc._task_trade_flags(no_pos_session, task)
    assert flags["has_orders"] is True
    assert flags["has_open_position"] is False
    assert flags["can_edit"] is False
    assert flags["can_edit_rules"] is True

    with_pos_session = AsyncMock()
    with_pos_session.execute = AsyncMock(
        side_effect=[
            SimpleNamespace(scalar_one=lambda: 3),
            SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(quantity=2)),
        ]
    )
    flags2 = await task_svc._task_trade_flags(with_pos_session, task)
    assert flags2["has_open_position"] is True
    assert flags2["can_edit"] is False
    assert flags2["can_edit_rules"] is False
