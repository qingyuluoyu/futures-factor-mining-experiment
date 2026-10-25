"""AI 渠道商 REST API —— 增删查 + 测试连通性"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_session
from app.models.ai_model import AIModel
from app.models.ai_provider import AIProvider
from app.models.user import User
from app.schemas.ai_provider import (
    PresetProvider,
    ProviderCreate,
    ProviderResponse,
    ProviderTestResult,
    ProviderUpdate,
)
from app.services.crypto import decrypt, encrypt, mask_key
from app.services.ai.providers.urls import resolve_endpoint

router = APIRouter(prefix="/api/ai/providers", tags=["AI 渠道管理"])

# 预设渠道配置
PRESET_PROVIDERS: list[PresetProvider] = [
    PresetProvider(
        name="OpenAI",
        api_type="openai",
        base_url="https://api.openai.com",
        description="GPT 系列，支持工具调用和多模态",
    ),
    PresetProvider(
        name="Anthropic",
        api_type="claude",
        base_url="https://api.anthropic.com",
        description="Claude 系列，支持工具调用、多模态和思考",
    ),
    PresetProvider(
        name="MiniMax",
        api_type="claude",
        base_url="https://api.minimax.cn/anthropic",
        description=(
            "MiniMax M3/M2 系列，Anthropic 兼容接口；"
            "API Key 在 platform.minimax.cn 获取，M3 支持思考开关"
        ),
    ),
    PresetProvider(
        name="DeepSeek",
        api_type="openai",
        base_url="https://api.deepseek.com",
        description="DeepSeek 系列，性价比高",
    ),
    PresetProvider(
        name="Google Gemini",
        api_type="openai",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        description="Gemini 系列，Google 出品",
    ),
    PresetProvider(
        name="通义千问",
        api_type="openai",
        base_url="https://dashscope.aliyuncs.com/compatible-mode",
        description="通义千问系列，阿里云出品",
    ),
    PresetProvider(
        name="OpenCode GO",
        api_type="openai",
        base_url="https://opencode.ai/zen/go",
        description=(
            "OpenCode Zen GO 线路：MiniMax/Kimi/GLM/DeepSeek/Qwen 等，"
            "OpenAI 兼容；API Key 在 opencode.ai/auth 获取，"
            "添加后在模型管理里「从渠道添加模型」"
        ),
    ),
    PresetProvider(
        name="TypeSafe Jev",
        api_type="jev",
        base_url="https://api.typesafe.ai",
        description=(
            "System One 决策模型 Jev（jev-latest）：返回带概率的"
            "结构化判断而非生成文本；API Key 在 console.typesafe.ai/keys 获取，"
            "添加后点「测试连接」拉取可用模型"
        ),
    ),
]


def _safe_decrypt_mask(encrypted: str | None) -> str:
    """安全解密并脱敏，解密失败时返回占位符"""
    if not encrypted:
        return "****"
    try:
        return mask_key(decrypt(encrypted))
    except Exception:
        return "**** (需重新配置)"


# MiniMax Anthropic 兼容层不提供 /v1/models 端点，模型目录按官方文档维护
MINIMAX_MODEL_CATALOG: list[str] = [
    "MiniMax-M3",
    "MiniMax-M2.7",
    "MiniMax-M2.7-highspeed",
    "MiniMax-M2.5",
    "MiniMax-M2.5-highspeed",
    "MiniMax-M2.1",
    "MiniMax-M2.1-highspeed",
    "MiniMax-M2",
]


def _is_minimax_url(base_url: str) -> bool:
    """按 base_url 判定 MiniMax 官方域（国内 api.minimax.cn / 海外 api.minimaxi.com）"""
    u = base_url.lower()
    return "minimax.cn" in u or "minimaxi.com" in u


def _provider_to_response(
    provider: AIProvider,
    model_count: int,
) -> ProviderResponse:
    """ORM 对象转响应模型"""
    return ProviderResponse(
        id=provider.id,
        user_id=provider.user_id,
        name=provider.name,
        api_type=provider.api_type,
        base_url=provider.base_url,
        api_key=_safe_decrypt_mask(provider.api_key),
        is_active=provider.is_active,
        thinking_enabled=provider.thinking_enabled,
        created_at=provider.created_at,
        model_count=model_count,
    )


@router.get("/presets", response_model=list[PresetProvider])
async def get_preset_providers() -> list[PresetProvider]:
    """获取预设渠道列表"""
    return PRESET_PROVIDERS


@router.post("", response_model=ProviderResponse, status_code=status.HTTP_201_CREATED)
async def create_provider(
    body: ProviderCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ProviderResponse:
    """添加 AI 渠道"""
    provider = AIProvider(
        user_id=current_user.id,
        name=body.name,
        api_type=body.api_type,
        base_url=body.base_url.strip().rstrip("/"),
        api_key=encrypt(body.api_key),
        thinking_enabled=body.thinking_enabled,
    )
    session.add(provider)
    await session.commit()
    await session.refresh(provider)
    return _provider_to_response(provider, 0)


@router.get("", response_model=list[ProviderResponse])
async def list_providers(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[ProviderResponse]:
    """获取当前用户的所有渠道"""
    stmt = (
        select(AIProvider)
        .where(AIProvider.user_id == current_user.id)
        .order_by(AIProvider.created_at)
    )
    result = await session.execute(stmt)
    providers = result.scalars().all()

    # 批量查询各渠道模型数量
    count_stmt = (
        select(AIModel.provider_id, func.count(AIModel.id))
        .where(AIModel.provider_id.in_([p.id for p in providers]))
        .group_by(AIModel.provider_id)
    )
    count_result = await session.execute(count_stmt)
    count_map = dict(count_result.all())

    return [
        _provider_to_response(p, count_map.get(p.id, 0))
        for p in providers
    ]


async def _get_user_provider(
    session: AsyncSession,
    provider_id: str,
    user_id: UUID,
) -> AIProvider:
    """按 ID 取当前用户渠道，无效 ID / 不存在则抛 HTTP 异常"""
    try:
        uid = UUID(provider_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的 ID 格式")

    stmt = select(AIProvider).where(
        AIProvider.id == uid,
        AIProvider.user_id == user_id,
    )
    result = await session.execute(stmt)
    provider = result.scalar_one_or_none()
    if provider is None:
        raise HTTPException(status_code=404, detail="渠道不存在")
    return provider


@router.put("/{provider_id}", response_model=ProviderResponse)
async def update_provider(
    provider_id: str,
    body: ProviderUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ProviderResponse:
    """更新渠道（名称 / 规范 / URL / Key / 启停）"""
    provider = await _get_user_provider(session, provider_id, current_user.id)

    if body.name is not None:
        provider.name = body.name.strip()
    if body.api_type is not None:
        provider.api_type = body.api_type
    if body.base_url is not None:
        provider.base_url = body.base_url.strip().rstrip("/")
    # api_key 仅在传入非空字符串时更新，避免误清空
    if body.api_key is not None and body.api_key.strip():
        provider.api_key = encrypt(body.api_key.strip())
    if body.is_active is not None:
        provider.is_active = body.is_active
    # 三态字段：显式传 null 表示恢复「跟随模型自动」，需用 model_fields_set 区分「未传」
    if "thinking_enabled" in body.model_fields_set:
        provider.thinking_enabled = body.thinking_enabled

    await session.commit()
    await session.refresh(provider)

    count_stmt = select(func.count(AIModel.id)).where(
        AIModel.provider_id == provider.id
    )
    count_result = await session.execute(count_stmt)
    model_count = int(count_result.scalar_one() or 0)
    return _provider_to_response(provider, model_count)


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(
    provider_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> None:
    """删除渠道及其下所有模型"""
    provider = await _get_user_provider(session, provider_id, current_user.id)
    uid = provider.id

    # 级联删除模型
    await session.execute(
        delete(AIModel).where(AIModel.provider_id == uid)
    )
    await session.delete(provider)
    await session.commit()


@router.post("/{provider_id}/test", response_model=ProviderTestResult)
async def test_provider(
    provider_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ProviderTestResult:
    """测试渠道连通性 —— 调用 /v1/models 端点

    解密失败（AES 密钥变更/密文损坏）时返回业务错误，不抛 500。
    """
    import httpx

    provider = await _get_user_provider(session, provider_id, current_user.id)
    try:
        api_key = decrypt(provider.api_key)
    except Exception:
        return ProviderTestResult(
            status="error",
            message="API Key 解密失败（密钥可能因 AES_ENCRYPTION_KEY 变更失效）。请点「编辑」重新填写 API Key 后保存再测。",
        )

    base_url = provider.base_url.strip().rstrip("/")

    try:
        if provider.api_type == "openai":
            url = resolve_endpoint(base_url, "/models")
            headers = {"Authorization": f"Bearer {api_key}"}
        elif provider.api_type == "jev":
            # TypeSafe System One：Bearer 认证，目录端点返回 models[].name
            url = resolve_endpoint(base_url, "/models")
            headers = {"Authorization": f"Bearer {api_key}"}
        else:
            # Claude 规范
            url = resolve_endpoint(base_url, "/models")
            headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            }

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=headers)

            # MiniMax Anthropic 兼容层无 /v1/models：改用最小 messages 请求探活，
            # 成功则返回预设模型目录，保证「从渠道添加模型」可用
            if (
                provider.api_type == "claude"
                and resp.status_code in (404, 405)
                and _is_minimax_url(base_url)
            ):
                probe = await client.post(
                    resolve_endpoint(base_url, "/messages"),
                    headers={**headers, "Content-Type": "application/json"},
                    json={
                        "model": MINIMAX_MODEL_CATALOG[0],
                        "max_tokens": 1,
                        "messages": [{"role": "user", "content": "hi"}],
                    },
                )
                if probe.status_code == 200:
                    return ProviderTestResult(
                        status="ok",
                        message=f"连接成功，共 {len(MINIMAX_MODEL_CATALOG)} 个可用模型",
                        models=list(MINIMAX_MODEL_CATALOG),
                    )
                return ProviderTestResult(
                    status="error",
                    message=f"HTTP {probe.status_code}: {probe.text[:200]}",
                )

            if resp.status_code != 200:
                return ProviderTestResult(
                    status="error",
                    message=f"HTTP {resp.status_code}: {resp.text[:200]}",
                )

            data = resp.json()
            models: list[str] = []
            if provider.api_type == "jev":
                # TypeSafe 目录格式：{"models": [{"name": "jev-latest", ...}]}
                for item in data.get("models", []):
                    if isinstance(item, dict) and item.get("name"):
                        models.append(str(item["name"]))
            else:
                # OpenAI / Claude models 列表均常见 data[].id
                for item in data.get("data", []):
                    if isinstance(item, dict) and item.get("id"):
                        models.append(str(item["id"]))

            return ProviderTestResult(
                status="ok",
                message=f"连接成功，共 {len(models)} 个可用模型",
                models=models,
            )
    except httpx.ConnectError:
        return ProviderTestResult(status="error", message="网络连接失败")
    except httpx.TimeoutException:
        return ProviderTestResult(status="error", message="连接超时")
    except Exception as e:
        return ProviderTestResult(status="error", message=str(e)[:200])
