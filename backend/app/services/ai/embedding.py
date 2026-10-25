"""向量嵌入服务 —— 使用已配置的 AI Provider 生成文本嵌入"""

import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_model import AIModel
from app.models.ai_provider import AIProvider
from app.services.crypto import decrypt
from app.services.ai.providers.urls import resolve_endpoint

logger = logging.getLogger(__name__)

# 默认嵌入模型和维度
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSION = 1536


async def get_embedding(
    session: AsyncSession,
    user_id: str,
    text: str,
) -> list[float]:
    """为文本生成嵌入向量"""
    provider_info = await _find_embedding_provider(session, user_id)
    if provider_info is None:
        # 降级：使用简单的哈希向量（仅用于开发）
        logger.warning("无可用的嵌入 Provider，使用零向量降级")
        return [0.0] * EMBEDDING_DIMENSION

    base_url, api_key = provider_info
    url = resolve_endpoint(base_url, "/embeddings")

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": DEFAULT_EMBEDDING_MODEL,
                "input": text[:2000],  # 截断过长文本
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["data"][0]["embedding"]


async def get_embeddings_batch(
    session: AsyncSession,
    user_id: str,
    texts: list[str],
) -> list[list[float]]:
    """批量生成嵌入向量"""
    if not texts:
        return []

    provider_info = await _find_embedding_provider(session, user_id)
    if provider_info is None:
        logger.warning("无可用的嵌入 Provider，使用零向量降级")
        return [[0.0] * EMBEDDING_DIMENSION for _ in texts]

    base_url, api_key = provider_info
    url = resolve_endpoint(base_url, "/embeddings")

    # 分批处理（每批最多 20 条）
    all_embeddings: list[list[float]] = []
    batch_size = 20
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": DEFAULT_EMBEDDING_MODEL,
                    "input": [t[:2000] for t in batch],
                },
            )
            response.raise_for_status()
            data = response.json()
            # 按 index 排序确保顺序正确
            sorted_data = sorted(data["data"], key=lambda x: x["index"])
            all_embeddings.extend([d["embedding"] for d in sorted_data])

    return all_embeddings


async def _find_embedding_provider(
    session: AsyncSession,
    user_id: str,
) -> tuple[str, str] | None:
    """查找可用的嵌入 Provider（优先 OpenAI 类型）"""
    from uuid import UUID

    stmt = (
        select(AIProvider)
        .where(
            AIProvider.user_id == UUID(user_id),
            AIProvider.is_active == True,
            AIProvider.api_type == "openai",
        )
        .limit(1)
    )
    result = await session.execute(stmt)
    provider = result.scalar_one_or_none()

    if provider is None:
        return None

    api_key = decrypt(provider.api_key)
    return (provider.base_url, api_key)
