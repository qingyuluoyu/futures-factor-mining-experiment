"""通知 / 价格预警服务单元测试（纯函数 + mock session）"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.user_notify_setting import UserNotifySetting
from app.services.notify.notify_svc import (
    create_notification,
    is_category_enabled,
)
from app.services.notify.price_alert_svc import _is_hit


def test_is_category_enabled_flags() -> None:
    """trade/fund 受开关；system/risk 总开"""
    s = UserNotifySetting(
        notify_trade=False, notify_fund=True, notify_price_alert=True
    )
    assert is_category_enabled(s, "trade") is False
    assert is_category_enabled(s, "fund") is True
    assert is_category_enabled(s, "system") is True
    assert is_category_enabled(s, "risk") is True


def test_is_category_enabled_all_on() -> None:
    s = UserNotifySetting(notify_trade=True, notify_fund=True, notify_price_alert=True)
    for c in ("system", "risk", "trade", "fund"):
        assert is_category_enabled(s, c) is True


@pytest.mark.parametrize(
    "direction,target,last,hit",
    [
        ("above", Decimal("3000"), Decimal("3001"), True),
        ("above", Decimal("3000"), Decimal("3000"), True),  # 等于算命中
        ("above", Decimal("3000"), Decimal("2999"), False),
        ("below", Decimal("3000"), Decimal("2999"), True),
        ("below", Decimal("3000"), Decimal("3000"), True),
        ("below", Decimal("3000"), Decimal("3001"), False),
    ],
)
def test_is_hit(direction: str, target: Decimal, last: Decimal, hit: bool) -> None:
    assert _is_hit(direction, target, last) is hit


@pytest.mark.asyncio
async def test_create_notification_respect_setting_blocked() -> None:
    """开关关闭时 create_notification 返回 None 且不写库"""
    session = AsyncMock()
    session.add = AsyncMock()
    session.flush = AsyncMock()
    setting = UserNotifySetting(
        notify_trade=False, notify_fund=True, notify_price_alert=True
    )
    res = await create_notification(
        session,
        user_id=None,  # type: ignore[arg-type]  # 测试不走真实落库
        trading_mode="live",
        category="trade",
        title="t",
        content="c",
        respect_setting=setting,
    )
    assert res is None
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_create_notification_allowed() -> None:
    """开关开启时正常落库"""
    session = AsyncMock()
    session.add = SimpleNamespace  # add 是同步方法，用普通 mock
    added: list = []

    def _add(obj):  # noqa: ANN001
        added.append(obj)

    session.add = _add  # type: ignore[assignment]
    session.flush = AsyncMock()
    setting = UserNotifySetting(
        notify_trade=True, notify_fund=True, notify_price_alert=True
    )
    res = await create_notification(
        session,
        user_id=None,  # type: ignore[arg-type]
        trading_mode="virtual",
        category="trade",
        title="t",
        content="c",
        respect_setting=setting,
    )
    assert res is not None
    assert len(added) == 1
    session.flush.assert_awaited()
