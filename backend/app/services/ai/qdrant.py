"""Qdrant 向量数据库服务 —— 集合管理、向量存储与检索"""

import logging
from uuid import uuid4

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    PointStruct,
    VectorParams,
    PointIdsList,
    Filter,
    FieldCondition,
    MatchValue,
)

from app.core.config import load_settings

logger = logging.getLogger(__name__)

# 集合名称
COLLECTION_LONG_TERM_MEMORY = "ai_long_term_memory"
COLLECTION_USER_PROFILE = "ai_user_profile"
COLLECTION_KNOWLEDGE = "ai_knowledge"

# 嵌入向量维度
VECTOR_DIMENSION = 1536

_client: AsyncQdrantClient | None = None


async def get_qdrant_client() -> AsyncQdrantClient:
    """获取 Qdrant 异步客户端（单例）"""
    global _client
    if _client is None:
        settings = load_settings()
        host = getattr(settings, "qdrant_host", "qdrant")
        port = getattr(settings, "qdrant_port", 6333)
        _client = AsyncQdrantClient(host=host, port=port)
        logger.info(f"Qdrant 客户端已连接: {host}:{port}")
    return _client


async def ensure_collections() -> None:
    """确保所有必需的集合存在"""
    client = await get_qdrant_client()
    collections = [c.name for c in (await client.get_collections()).collections]

    for name in [COLLECTION_LONG_TERM_MEMORY, COLLECTION_USER_PROFILE, COLLECTION_KNOWLEDGE]:
        if name not in collections:
            await client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=VECTOR_DIMENSION, distance=Distance.COSINE),
            )
            logger.info(f"Qdrant 集合已创建: {name}")


async def upsert_vectors(
    collection: str,
    points: list[dict],
) -> None:
    """存储向量点到集合"""
    client = await get_qdrant_client()
    await ensure_collections()

    qdrant_points = [
        PointStruct(
            id=str(p.get("id", uuid4())),
            vector=p["vector"],
            payload=p.get("payload", {}),
        )
        for p in points
    ]
    await client.upsert(collection_name=collection, points=qdrant_points)


async def search_similar(
    collection: str,
    query_vector: list[float],
    limit: int = 5,
    user_id_filter: str | None = None,
) -> list[dict]:
    """向量相似度搜索"""
    client = await get_qdrant_client()

    query_filter = None
    if user_id_filter:
        query_filter = Filter(
            must=[FieldCondition(key="user_id", match=MatchValue(value=user_id_filter))]
        )

    results = await client.search(
        collection_name=collection,
        query_vector=query_vector,
        query_filter=query_filter,
        limit=limit,
        score_threshold=0.5,
    )

    return [
        {"id": str(r.id), "score": r.score, "payload": r.payload}
        for r in results
    ]


async def delete_by_filter(
    collection: str,
    field: str,
    value: str,
) -> None:
    """按字段值删除向量"""
    client = await get_qdrant_client()
    await client.delete(
        collection_name=collection,
        points_selector=Filter(
            must=[FieldCondition(key=field, match=MatchValue(value=value))]
        ),
    )
