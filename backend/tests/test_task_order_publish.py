"""任务成交事件推送单测 —— payload 组装 / 失败不抛错 / 与通知联动"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.notify.trade_notify import (
    notify_order_filled,
    publish_task_order_filled,
)


def test_publish_payload_shape() -> None:
    """任务成交事件 payload 字段完整且类型正确"""
    user_id = uuid4()
    task_id = uuid4()
    order_id = uuid4()
    with patch("app.api.ws.broadcast_task_order") as bc:
        publish_task_order_filled(
            user_id,
            task_id,
            order_id,
            "live",
            "rb2610",
            "螺纹钢2610",
            "buy",
            "open",
            Decimal("3410.5"),
            5,
        )
    bc.assert_called_once()
    uid, data = bc.call_args[0]
    assert uid == str(user_id)
    assert data["task_id"] == str(task_id)
    assert data["order_id"] == str(order_id)
    assert data["symbol"] == "rb2610"
    assert data["direction"] == "buy"
    assert data["offset"] == "open"
    assert data["price"] == 3410.5
    assert data["filled_qty"] == 5
    assert "T" in data["time"] or "+" in data["time"]  # ISO 时间串


def test_publish_failure_swallowed() -> None:
    """推送抛错不得向上传播（不影响成交主流程）"""
    with patch(
        "app.api.ws.broadcast_task_order", side_effect=RuntimeError("redis down")
    ):
        publish_task_order_filled(
            uuid4(), uuid4(), uuid4(), "live", "rb2610", "螺纹钢", "sell",
            "close", Decimal("3400"), 3,
        )


@pytest.mark.asyncio
async def test_notify_order_filled_triggers_task_publish() -> None:
    """任务单成交：写通知之余触发 task_order_tick 推送；手动单不触发"""
    session = AsyncMock()
    session.execute.return_value = MagicMock(
        scalar_one_or_none=lambda: MagicMock(notify_trade=True)
    )
    user_id = uuid4()
    task_id = uuid4()

    with (
        patch("app.api.ws.broadcast_task_order") as bc,
    ):
        await notify_order_filled(
            session,
            user_id,
            "live",
            "rb2610",
            "螺纹钢2610",
            "buy",
            Decimal("3410"),
            5,
            "open",
            task_id=task_id,
            order_id=uuid4(),
        )
        assert bc.call_count == 1

        # 手动单（task_id=None）不推送任务事件
        bc.reset_mock()
        await notify_order_filled(
            session,
            user_id,
            "live",
            "rb2610",
            "螺纹钢2610",
            "sell",
            Decimal("3400"),
            3,
            "close",
        )
        assert bc.call_count == 0


@pytest.mark.asyncio
async def test_notify_order_filled_publish_error_does_not_raise() -> None:
    """推送异常被吞掉，不影响成交通知主流程"""
    session = AsyncMock()
    session.execute.return_value = MagicMock(
        scalar_one_or_none=lambda: MagicMock(notify_trade=True)
    )
    with patch(
        "app.services.notify.trade_notify.publish_task_order_filled",
        side_effect=RuntimeError("boom"),
    ):
        await notify_order_filled(
            session,
            uuid4(),
            "live",
            "rb2610",
            "螺纹钢2610",
            "buy",
            Decimal("3410"),
            1,
            "open",
            task_id=uuid4(),
            order_id=uuid4(),
        )
