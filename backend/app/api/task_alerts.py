"""任务预警 API —— AI/量化任务下单与平仓提醒设置读写

按 JWT (user, trading_mode) 隔离。事件本体经 ws:notify 频道按 user_id
定向推送（task_order_tick），本路由只负责用户设置。
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_auth_context, get_session
from app.schemas.task_alert import (
    TaskAlertSettingResponse,
    TaskAlertSettingUpdate,
)
from app.services.notify.task_alert_svc import get_or_create_setting, update_setting

router = APIRouter(prefix="/api/users/me/task-alerts", tags=["任务预警"])


@router.get("/settings", response_model=TaskAlertSettingResponse)
async def get_task_alert_setting_api(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    session: AsyncSession = Depends(get_session),
) -> TaskAlertSettingResponse:
    """读取当前盘任务预警设置（不存在则建默认行）"""
    row = await get_or_create_setting(session, ctx.user.id, ctx.trading_mode)
    return TaskAlertSettingResponse.model_validate(row)


@router.put("/settings", response_model=TaskAlertSettingResponse)
async def update_task_alert_setting_api(
    body: TaskAlertSettingUpdate,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    session: AsyncSession = Depends(get_session),
) -> TaskAlertSettingResponse:
    """更新任务预警设置（部分更新）"""
    row = await update_setting(
        session, ctx.user.id, ctx.trading_mode, body.model_dump(exclude_unset=True)
    )
    return TaskAlertSettingResponse.model_validate(row)
