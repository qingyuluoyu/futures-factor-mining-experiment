"""任务预警 Pydantic schema —— 设置读写"""

from datetime import datetime

from pydantic import BaseModel


class TaskAlertSettingResponse(BaseModel):
    """任务预警设置响应"""

    trading_mode: str
    open_enabled: bool
    close_enabled: bool
    popup_enabled: bool
    sound_enabled: bool
    voice_enabled: bool
    updated_at: datetime

    model_config = {"from_attributes": True}


class TaskAlertSettingUpdate(BaseModel):
    """更新任务预警设置（全部可选，部分更新）"""

    open_enabled: bool | None = None
    close_enabled: bool | None = None
    popup_enabled: bool | None = None
    sound_enabled: bool | None = None
    voice_enabled: bool | None = None
