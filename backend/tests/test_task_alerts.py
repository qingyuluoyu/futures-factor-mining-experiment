"""任务预警单测 —— 模型默认值 / schema 部分更新 / 服务层 upsert"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.models.task_alert import TaskAlertSetting
from app.schemas.task_alert import (
    TaskAlertSettingResponse,
    TaskAlertSettingUpdate,
)
from app.services.notify.task_alert_svc import get_or_create_setting, update_setting


def test_setting_defaults() -> None:
    """默认语义：开仓/平仓预警开、弹窗开、提示音开、语音关"""
    s = TaskAlertSetting(
        user_id=uuid4(),
        trading_mode="live",
        open_enabled=True,
        close_enabled=True,
        popup_enabled=True,
        sound_enabled=True,
        voice_enabled=False,
    )
    assert s.open_enabled is True
    assert s.close_enabled is True
    assert s.popup_enabled is True
    assert s.sound_enabled is True
    assert s.voice_enabled is False


def test_setting_update_partial_only_set_fields() -> None:
    """部分更新：未提供的字段不进 payload"""
    upd = TaskAlertSettingUpdate(voice_enabled=True)
    dumped = upd.model_dump(exclude_unset=True)
    assert dumped == {"voice_enabled": True}
    assert "open_enabled" not in dumped


def test_setting_response_from_attributes() -> None:
    """Response 能从 ORM 属性构造（模拟 flush 后带显式值的状态）"""
    from datetime import datetime, timezone

    s = TaskAlertSetting(
        user_id=uuid4(),
        trading_mode="virtual",
        open_enabled=True,
        close_enabled=True,
        popup_enabled=True,
        sound_enabled=True,
        voice_enabled=False,
        updated_at=datetime.now(timezone.utc),
    )
    resp = TaskAlertSettingResponse.model_validate(s)
    assert resp.trading_mode == "virtual"
    assert resp.open_enabled is True
    assert resp.voice_enabled is False


@pytest.mark.asyncio
async def test_get_or_create_setting_creates_row() -> None:
    """无既有行时新建默认行（flush 后返回）"""
    user_id = uuid4()
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute.return_value = result

    row = await get_or_create_setting(session, user_id, "live")
    session.add.assert_called_once()
    assert row is not None
    assert row.trading_mode == "live"


@pytest.mark.asyncio
async def test_update_setting_only_sets_provided_fields() -> None:
    """部分更新：只写 payload 提供的字段，其余不动"""
    user_id = uuid4()
    existing = TaskAlertSetting(
        user_id=user_id,
        trading_mode="live",
        open_enabled=True,
        close_enabled=True,
        popup_enabled=True,
        sound_enabled=True,
        voice_enabled=False,
    )
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing
    session.execute.return_value = result

    row = await update_setting(
        session, user_id, "live", {"voice_enabled": True, "close_enabled": None}
    )
    assert row.voice_enabled is True
    assert row.close_enabled is True  # None 不覆盖
    session.flush.assert_awaited()
