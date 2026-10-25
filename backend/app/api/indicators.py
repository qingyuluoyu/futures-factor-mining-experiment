"""图表指标配置 REST API —— 每用户一份，GET 读取 / PUT 整体替换"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_session
from app.models.user import User
from app.schemas.indicator import IndicatorConfigSchema
from app.services.indicator.settings_store import get_settings, upsert_settings

router = APIRouter(prefix="/api/indicators", tags=["图表指标配置"])


@router.get("/settings", response_model=IndicatorConfigSchema)
async def get_indicator_settings(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> IndicatorConfigSchema:
    """读取当前用户的图表指标配置（无记录返回默认，不入库）。"""
    config = await get_settings(session, current_user.id)
    return IndicatorConfigSchema.model_validate(config)


@router.put("/settings", response_model=IndicatorConfigSchema)
async def update_indicator_settings(
    body: IndicatorConfigSchema,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> IndicatorConfigSchema:
    """保存当前用户的图表指标配置（整体替换，归一化后落库）。"""
    saved = await upsert_settings(session, current_user.id, body.model_dump())
    return IndicatorConfigSchema.model_validate(saved)
