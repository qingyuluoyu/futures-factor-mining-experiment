"""任务 K 线交易标记单测 —— 字段映射 / 过滤口径 / 排序"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.ai_trading.task.history_svc import list_task_trade_marks


def _order(
    direction: str,
    offset: str,
    price: str,
    qty: int,
    filled_at: datetime,
    status: str = "filled",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        symbol="rb2610",
        direction=direction,
        offset=offset,
        price=Decimal(price),
        filled_qty=qty,
        status=status,
        filled_at=filled_at,
        created_at=filled_at,
    )


@pytest.mark.asyncio
async def test_trade_marks_mapping_and_order() -> None:
    """已成交委托按 filled_at 升序输出最小字段集"""
    user_id = uuid4()
    task_id = uuid4()
    t1 = datetime(2026, 8, 10, 1, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 8, 11, 2, 30, tzinfo=timezone.utc)
    rows = [
        _order("buy", "open", "3410", 5, t1),     # 早成交（SQL 升序第一位）
        _order("sell", "close", "3380", 3, t2),   # 晚成交（升序第二位）
    ]
    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    session.execute.return_value = result

    with patch(
        "app.services.ai_trading.task.history_svc.get_task", new=AsyncMock()
    ):
        data = await list_task_trade_marks(session, user_id, task_id)

    assert data["total"] == 2
    first, second = data["items"]
    assert first["direction"] == "buy"
    assert first["offset"] == "open"
    assert first["price"] == 3410.0
    assert first["filled_qty"] == 5
    assert first["task_id"] == str(task_id)
    assert first["time"]  # ISO 字符串非空
    assert second["direction"] == "sell"
    assert second["offset"] == "close"


@pytest.mark.asyncio
async def test_trade_marks_falls_back_to_created_at() -> None:
    """filled_at 缺失时回退 created_at（理论不该发生，防御口径）"""
    user_id = uuid4()
    task_id = uuid4()
    t = datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)
    row = _order("buy", "open", "3410", 2, t)
    row.filled_at = None
    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [row]
    session.execute.return_value = result

    with patch(
        "app.services.ai_trading.task.history_svc.get_task", new=AsyncMock()
    ):
        data = await list_task_trade_marks(session, user_id, task_id)

    assert data["items"][0]["time"]  # 仍有时间可用


@pytest.mark.asyncio
async def test_trade_marks_ownership_check_via_get_task() -> None:
    """任务归属校验：get_task 抛错时向上传播（API 层转 404/403）"""
    from fastapi import HTTPException

    session = AsyncMock()
    with patch(
        "app.services.ai_trading.task.history_svc.get_task",
        new=AsyncMock(side_effect=HTTPException(status_code=404)),
    ):
        with pytest.raises(HTTPException):
            await list_task_trade_marks(session, uuid4(), uuid4())
