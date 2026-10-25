from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.api.users import router
from app.core.deps import get_current_user, get_session
from app.models.user_notify_setting import UserNotifySetting
from app.schemas.notify import NotifySettingUpdate
from app.services.notify.notify_svc import is_category_enabled


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None, [], {}])
def test_popup_update_requires_actual_boolean(value):
    with pytest.raises(ValidationError):
        NotifySettingUpdate.model_validate({"notify_popup_enabled": value})


def test_popup_column_has_compatible_true_default():
    column = UserNotifySetting.__table__.columns.get("notify_popup_enabled")
    assert column is not None
    assert column.nullable is False
    assert str(column.server_default.arg) == "true"


def test_popup_does_not_change_notification_generation():
    setting = UserNotifySetting(notify_trade=True, notify_fund=True, notify_price_alert=True)
    setting.notify_popup_enabled = False
    for category in ("system", "risk", "trade", "fund"):
        assert is_category_enabled(setting, category)


@pytest.mark.asyncio
async def test_notification_http_rejects_coercion_before_database_access():
    app = FastAPI()
    app.include_router(router)
    session = AsyncMock()
    session.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(notify_trade=True, notify_fund=True, notify_price_alert=True, notify_sound_enabled=True))
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid4())
    app.dependency_overrides[get_session] = lambda: session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put("/api/users/me/notify-settings", json={"notify_popup_enabled": "false"})
    assert response.status_code == 422
    session.execute.assert_not_awaited()
