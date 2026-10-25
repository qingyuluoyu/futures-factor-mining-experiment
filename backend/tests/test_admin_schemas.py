"""管理后台 Schema 校验测试"""

import pytest
from pydantic import ValidationError

from app.schemas.admin import (
    AdminUserCreateRequest,
    AdminUserUpdateRequest,
    SystemSettingsUpdateRequest,
)


def test_create_user_role_and_status() -> None:
    body = AdminUserCreateRequest(
        username="trader_01",
        password="secret12",
        role="admin",
        status="active",
    )
    assert body.username == "trader_01"
    assert body.role == "admin"


def test_create_user_rejects_bad_username() -> None:
    with pytest.raises(ValidationError):
        AdminUserCreateRequest(username="bad name", password="secret12")


def test_create_user_rejects_bad_role() -> None:
    with pytest.raises(ValidationError):
        AdminUserCreateRequest(
            username="user01",
            password="secret12",
            role="super",
        )


def test_update_user_partial() -> None:
    body = AdminUserUpdateRequest(status="frozen")
    assert body.status == "frozen"
    assert body.role is None


def test_settings_update_period() -> None:
    body = SystemSettingsUpdateRequest(
        registration_enabled=False,
        daily_register_limit=10,
        register_reset_hour=6,
        paper_claim_amount=500000,
        paper_claim_period="daily",
        paper_total_claim_cap=2000000,
    )
    assert body.paper_claim_period == "daily"
    assert body.registration_enabled is False


def test_settings_rejects_bad_period() -> None:
    with pytest.raises(ValidationError):
        SystemSettingsUpdateRequest(paper_claim_period="weekly")


# ---------- ctp_sim（CTP 中转模拟）渠道校验 ----------

def test_settings_channel_accepts_ctp_sim() -> None:
    body = SystemSettingsUpdateRequest(market_data_channel="ctp_sim")
    assert body.market_data_channel == "ctp_sim"


def test_settings_backup_channel_rejects_ctp_sim() -> None:
    """ctp_sim 是 7×24 模拟渠道，不得作实盘备用"""
    with pytest.raises(ValidationError):
        SystemSettingsUpdateRequest(backup_channel="ctp_sim")


def test_settings_ctp_sim_front_requires_tcp_scheme() -> None:
    body = SystemSettingsUpdateRequest(ctp_sim_md_front="tcp://h:1")
    assert body.ctp_sim_md_front == "tcp://h:1"
    with pytest.raises(ValidationError):
        SystemSettingsUpdateRequest(ctp_sim_md_front="http://h:1")
    with pytest.raises(ValidationError):
        SystemSettingsUpdateRequest(ctp_sim_md_front="ws://h:1")
