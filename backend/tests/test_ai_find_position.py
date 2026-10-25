"""find_symbol_position 回归测试

防止 execute.py 里再次用错 ORM 属性名（available_quantity vs available_qty）。
历史 bug：execute.py:81 直接访问 pos.available_quantity（不存在的 ORM 属性），
导致每个有持仓的 AI 任务评估时抛 AttributeError，评估中断、分析记录全部消失。
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.models.paper.position import PaperPosition
from app.services.ai_trading.core.execute import find_symbol_position


def _make_position(*, available_qty: int = 3, quantity: int = 5) -> PaperPosition:
    """构造真实 ORM 对象（不落库），属性名必须是 available_qty"""
    return PaperPosition(
        id=uuid4(),
        user_id=uuid4(),
        account_id=uuid4(),
        symbol="rb2510",
        symbol_name="螺纹钢2510",
        direction="long",
        source="ai",
        task_id=uuid4(),
        quantity=quantity,
        available_qty=available_qty,
        avg_price=Decimal("3100"),
        margin=Decimal("15500"),
    )


@pytest.mark.asyncio
async def test_find_symbol_position_uses_correct_orm_attribute() -> None:
    """find_symbol_position 的 task_id 分支应读 ORM 的 available_qty，不抛 AttributeError

    回归点：修复前访问 pos.available_quantity 会抛
    AttributeError: 'PaperPosition' object has no attribute 'available_quantity'
    """
    pos = _make_position(available_qty=3, quantity=5)

    # session.execute 是 async，返回的 Result.scalar_one_or_none 是同步方法
    result = SimpleNamespace(scalar_one_or_none=lambda: pos)
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)

    out = await find_symbol_position(
        session,
        user_id=UUID(int=1),
        symbol="rb2510",
        trading_mode="live",
        source="ai",
        task_id=UUID(int=2),
    )

    # 不抛异常即说明用对了 ORM 属性名（核心回归断言）
    assert out is not None
    # dict key 名对齐下游 engine/risk/策略读取
    assert out["available_quantity"] == 3
    assert out["quantity"] == 5
    assert out["direction"] == "long"


@pytest.mark.asyncio
async def test_find_symbol_position_returns_none_when_no_position() -> None:
    """无持仓时返回 None"""
    result = SimpleNamespace(scalar_one_or_none=lambda: None)
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)

    out = await find_symbol_position(
        session,
        user_id=UUID(int=1),
        symbol="rb2510",
        trading_mode="live",
        source="ai",
        task_id=UUID(int=2),
    )
    assert out is None
