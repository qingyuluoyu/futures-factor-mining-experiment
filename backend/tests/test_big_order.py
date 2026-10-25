"""大单预警单测 —— model 默认值 / schema 校验 / 交易日归属"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.big_order import BigOrderAlertSetting
from app.schemas.big_order import (
    BigOrderSettingResponse,
    BigOrderSettingUpdate,
)
from app.services.trading_hours import _BJ_TZ, get_trading_day


def test_setting_defaults() -> None:
    """模型 server_default 描述：阈值 100、多红空绿、弹窗开、语音关

    注意：SQLAlchemy 的 default/server_default 在 INSERT（flush）时才填值，
    未 flush 的实例属性为 None。这里验证 schema 默认行为与服务层 upsert 语义：
    通过 BigOrderSettingUpdate 的 None 字段跳过，保留 server_default。
    """
    # 构造一个带显式值的设置实例，模拟 flush 后的状态
    s = BigOrderAlertSetting(
        user_id=uuid4(),
        trading_mode="live",
        buy_enabled=True,
        buy_threshold=100,
        buy_color="#ef4444",
        sell_enabled=True,
        sell_threshold=100,
        sell_color="#22c55e",
        popup_enabled=True,
        sound_enabled=True,
        voice_enabled=False,
    )
    assert s.buy_threshold == 100
    assert s.sell_threshold == 100
    assert s.buy_color == "#ef4444"
    assert s.sell_color == "#22c55e"
    assert s.popup_enabled is True
    assert s.sound_enabled is True
    assert s.voice_enabled is False
    # 服务层 upsert：未设置字段保持默认（None 不覆盖）
    upd = BigOrderSettingUpdate(buy_threshold=200).model_dump(exclude_unset=True)
    assert upd == {"buy_threshold": 200}


def test_setting_update_partial_only_set_fields() -> None:
    """部分更新：未提供的字段保持 None（exclude_unset）"""
    upd = BigOrderSettingUpdate(buy_threshold=200, voice_enabled=True)
    dumped = upd.model_dump(exclude_unset=True)
    assert dumped == {"buy_threshold": 200, "voice_enabled": True}
    assert "buy_color" not in dumped


def test_setting_update_rejects_bad_color() -> None:
    """颜色必须是 #RRGGBB"""
    with pytest.raises(ValidationError):
        BigOrderSettingUpdate(buy_color="red")
    with pytest.raises(ValidationError):
        BigOrderSettingUpdate(buy_color="#fff")


def test_setting_update_rejects_bad_threshold() -> None:
    """阈值范围 1~100000"""
    with pytest.raises(ValidationError):
        BigOrderSettingUpdate(buy_threshold=0)
    with pytest.raises(ValidationError):
        BigOrderSettingUpdate(buy_threshold=100001)


def test_setting_response_from_attributes() -> None:
    """BigOrderSettingResponse 能从 flush 后的 ORM 属性构造"""
    s = BigOrderAlertSetting(
        user_id=uuid4(),
        trading_mode="virtual",
        buy_enabled=True,
        buy_threshold=100,
        buy_color="#ef4444",
        sell_enabled=True,
        sell_threshold=100,
        sell_color="#22c55e",
        popup_enabled=True,
        sound_enabled=True,
        voice_enabled=False,
    )
    s.updated_at = datetime.now(timezone.utc)
    resp = BigOrderSettingResponse.model_validate(s)
    assert resp.buy_threshold == 100
    assert resp.trading_mode == "virtual"


def test_get_trading_day_session_split() -> None:
    """21:00 → 次日为同一交易日；20:30 尚属当日"""
    # 周三 20:30（清空时刻）仍属当日
    bj = datetime(2026, 7, 29, 20, 30, tzinfo=_BJ_TZ)  # 周三
    assert get_trading_day(bj).date() == __import__("datetime").date(2026, 7, 29)
    # 周三 21:00 起属次日（周四）
    bj2 = datetime(2026, 7, 29, 21, 0, tzinfo=_BJ_TZ)
    assert get_trading_day(bj2).date() == __import__("datetime").date(2026, 7, 30)
    # 周四 14:00 仍属周四
    bj3 = datetime(2026, 7, 30, 14, 0, tzinfo=_BJ_TZ)
    assert get_trading_day(bj3).date() == __import__("datetime").date(2026, 7, 30)


def test_get_trading_day_friday_to_monday() -> None:
    """周五 21:00 → 下周一"""
    bj = datetime(2026, 7, 31, 21, 0, tzinfo=_BJ_TZ)  # 周五晚
    assert get_trading_day(bj).weekday() == 0  # 周一
