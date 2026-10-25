"""管理员用户序列化与辅助函数单测"""

from datetime import datetime, timezone
from uuid import uuid4

from app.models.user import User
from app.services.admin.users_svc import user_to_admin_dict


def test_user_to_admin_dict_fields() -> None:
    user = User(
        id=uuid4(),
        username="alice",
        phone="13800000000",
        email="a@example.com",
        hashed_password="x",
        avatar=None,
        real_name_verified=False,
        role="user",
        status="active",
        created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    data = user_to_admin_dict(user, risk_rate=12.5, total_equity=1000.0, total_claimed=500.0)
    assert data["username"] == "alice"
    assert data["phone"] == "13800000000"
    assert data["role"] == "user"
    assert data["status"] == "active"
    assert data["risk_rate"] == 12.5
    assert data["total_equity"] == 1000.0
    assert data["total_claimed"] == 500.0
    assert data["id"] == str(user.id)
    assert data["created_at"].endswith("Z")
