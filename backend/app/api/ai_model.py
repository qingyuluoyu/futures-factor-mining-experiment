"""AI 模型库 REST API —— CRUD + 批量从渠道添加"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_session
from app.models.ai_model import AIModel
from app.models.ai_provider import AIProvider
from app.models.user import User
from app.schemas.ai_model import (
    ModelBatchCreate,
    ModelCreate,
    ModelResponse,
    ModelUpdate,
)

router = APIRouter(prefix="/api/ai/models", tags=["AI 模型库"])

# 预设模型能力表：model_id → 能力标签列表
PRESET_CAPABILITIES: dict[str, dict] = {
    # OpenAI
    "gpt-4o": {"capabilities": ["tool_calling", "vision"], "is_multimodal": True},
    "gpt-4o-mini": {"capabilities": ["tool_calling", "vision"], "is_multimodal": True},
    "gpt-4-turbo": {"capabilities": ["tool_calling", "vision"], "is_multimodal": True},
    "gpt-4": {"capabilities": ["tool_calling"], "is_multimodal": False},
    "o1": {"capabilities": ["tool_calling", "vision", "thinking"], "is_multimodal": True},
    "o1-pro": {"capabilities": ["tool_calling", "vision", "thinking"], "is_multimodal": True},
    "o3": {"capabilities": ["tool_calling", "vision", "thinking"], "is_multimodal": True},
    "o4-mini": {"capabilities": ["tool_calling", "vision", "thinking"], "is_multimodal": True},
    # Anthropic
    "claude-sonnet-4-20250514": {"capabilities": ["tool_calling", "vision", "thinking"], "is_multimodal": True},
    "claude-opus-4-20250514": {"capabilities": ["tool_calling", "vision", "thinking"], "is_multimodal": True},
    "claude-haiku-4-20250514": {"capabilities": ["tool_calling", "vision"], "is_multimodal": True},
    "claude-3-5-sonnet-20241022": {"capabilities": ["tool_calling", "vision"], "is_multimodal": True},
    "claude-3-5-haiku-20241022": {"capabilities": ["tool_calling", "vision"], "is_multimodal": True},
    "claude-3-opus-20240229": {"capabilities": ["tool_calling", "vision"], "is_multimodal": True},
    # MiniMax（Anthropic 兼容接口；M2.x 思考常开不可关，M3 支持思考开关）
    "MiniMax-M3": {"capabilities": ["tool_calling", "vision", "thinking"], "is_multimodal": True},
    "MiniMax-M2.7": {"capabilities": ["tool_calling", "thinking"], "is_multimodal": False},
    "MiniMax-M2.7-highspeed": {"capabilities": ["tool_calling", "thinking"], "is_multimodal": False},
    "MiniMax-M2.5": {"capabilities": ["tool_calling", "thinking"], "is_multimodal": False},
    "MiniMax-M2.5-highspeed": {"capabilities": ["tool_calling", "thinking"], "is_multimodal": False},
    "MiniMax-M2.1": {"capabilities": ["tool_calling", "thinking"], "is_multimodal": False},
    "MiniMax-M2.1-highspeed": {"capabilities": ["tool_calling", "thinking"], "is_multimodal": False},
    "MiniMax-M2": {"capabilities": ["tool_calling", "thinking"], "is_multimodal": False},
    # DeepSeek
    "deepseek-chat": {"capabilities": ["tool_calling"], "is_multimodal": False},
    "deepseek-reasoner": {"capabilities": ["tool_calling", "thinking"], "is_multimodal": False},
    # Google
    "gemini-2.5-pro": {"capabilities": ["tool_calling", "vision", "thinking"], "is_multimodal": True},
    "gemini-2.5-flash": {"capabilities": ["tool_calling", "vision", "thinking"], "is_multimodal": True},
    "gemini-2.0-flash": {"capabilities": ["tool_calling", "vision"], "is_multimodal": True},
    # 通义千问
    "qwen-max": {"capabilities": ["tool_calling", "vision"], "is_multimodal": True},
    "qwen-plus": {"capabilities": ["tool_calling", "vision"], "is_multimodal": True},
    "qwen-turbo": {"capabilities": ["tool_calling"], "is_multimodal": False},
    "qwen-vl-max": {"capabilities": ["tool_calling", "vision"], "is_multimodal": True},
    "qwen3-235b-a22b": {"capabilities": ["tool_calling", "thinking"], "is_multimodal": False},
    # TypeSafe Jev（System One 决策模型：返回结构化判断+概率，不做对话生成）
    "jev-latest": {"capabilities": ["decision"], "is_multimodal": False},
    "jev-preview": {"capabilities": ["decision"], "is_multimodal": False},
}


def _get_preset_info(model_id: str) -> dict:
    """获取预设模型的能力信息，未知模型返回默认值"""
    if model_id in PRESET_CAPABILITIES:
        return PRESET_CAPABILITIES[model_id]
    return {"capabilities": ["tool_calling"], "is_multimodal": False}


def _model_to_response(
    model: AIModel,
    provider_name: str,
    provider_api_type: str,
) -> ModelResponse:
    """ORM 对象转响应模型"""
    caps = model.capabilities if isinstance(model.capabilities, list) else []
    return ModelResponse(
        id=model.id,
        user_id=model.user_id,
        provider_id=model.provider_id,
        model_id=model.model_id,
        display_name=model.display_name,
        is_multimodal=model.is_multimodal,
        is_custom=model.is_custom,
        capabilities=caps,
        created_at=model.created_at,
        provider_name=provider_name,
        provider_api_type=provider_api_type,
    )


@router.get("", response_model=list[ModelResponse])
async def list_models(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[ModelResponse]:
    """获取当前用户的所有模型"""
    stmt = (
        select(AIModel, AIProvider.name, AIProvider.api_type)
        .join(AIProvider, AIModel.provider_id == AIProvider.id)
        .where(AIModel.user_id == current_user.id)
        .order_by(AIModel.created_at)
    )
    result = await session.execute(stmt)
    rows = result.all()
    return [
        _model_to_response(model, provider_name, api_type)
        for model, provider_name, api_type in rows
    ]


@router.post("", response_model=ModelResponse, status_code=status.HTTP_201_CREATED)
async def create_model(
    body: ModelCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ModelResponse:
    """添加单个模型到模型库"""
    preset = _get_preset_info(body.model_id)
    model = AIModel(
        user_id=current_user.id,
        provider_id=UUID("00000000-0000-0000-0000-000000000000"),  # 占位，需指定渠道
        model_id=body.model_id,
        display_name=body.display_name,
        is_multimodal=body.is_multimodal or preset["is_multimodal"],
        is_custom=body.is_custom,
        capabilities=body.capabilities or preset["capabilities"],
    )
    raise HTTPException(status_code=400, detail="请通过 POST /api/ai/providers/{id}/models 添加模型")


@router.post("/providers/{provider_id}", response_model=list[ModelResponse], status_code=status.HTTP_201_CREATED)
async def batch_create_models(
    provider_id: str,
    body: ModelBatchCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[ModelResponse]:
    """从渠道批量添加模型"""
    try:
        uid = UUID(provider_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的 ID 格式")

    # 验证渠道归属
    stmt = select(AIProvider).where(
        AIProvider.id == uid,
        AIProvider.user_id == current_user.id,
    )
    result = await session.execute(stmt)
    provider = result.scalar_one_or_none()
    if provider is None:
        raise HTTPException(status_code=404, detail="渠道不存在")

    created: list[ModelResponse] = []
    for item in body.models:
        preset = _get_preset_info(item.model_id)
        model = AIModel(
            user_id=current_user.id,
            provider_id=uid,
            model_id=item.model_id,
            display_name=item.display_name,
            is_multimodal=item.is_multimodal or preset["is_multimodal"],
            is_custom=item.is_custom,
            capabilities=item.capabilities or preset["capabilities"],
        )
        session.add(model)
        await session.flush()
        await session.refresh(model)
        created.append(
            _model_to_response(model, provider.name, provider.api_type)
        )

    await session.commit()
    return created


@router.put("/{model_id}", response_model=ModelResponse)
async def update_model(
    model_id: str,
    body: ModelUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ModelResponse:
    """更新模型（修改显示名称）"""
    try:
        uid = UUID(model_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的 ID 格式")

    stmt = select(AIModel).where(
        AIModel.id == uid,
        AIModel.user_id == current_user.id,
    )
    result = await session.execute(stmt)
    model = result.scalar_one_or_none()
    if model is None:
        raise HTTPException(status_code=404, detail="模型不存在")

    model.display_name = body.display_name
    await session.commit()

    # 查询渠道信息
    p_stmt = select(AIProvider).where(AIProvider.id == model.provider_id)
    p_result = await session.execute(p_stmt)
    provider = p_result.scalar_one_or_none()
    p_name = provider.name if provider else ""
    p_type = provider.api_type if provider else ""

    return _model_to_response(model, p_name, p_type)


@router.delete("/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model(
    model_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> None:
    """删除模型"""
    try:
        uid = UUID(model_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的 ID 格式")

    stmt = select(AIModel).where(
        AIModel.id == uid,
        AIModel.user_id == current_user.id,
    )
    result = await session.execute(stmt)
    model = result.scalar_one_or_none()
    if model is None:
        raise HTTPException(status_code=404, detail="模型不存在")

    await session.delete(model)
    await session.commit()
