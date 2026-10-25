"""长期记忆服务 —— 对话摘要向量化、历史检索、用户偏好提取"""

import json
import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_conversation import AIConversation, AIMessage
from app.services.ai.embedding import get_embedding
from app.services.ai.qdrant import (
    COLLECTION_LONG_TERM_MEMORY,
    COLLECTION_USER_PROFILE,
    search_similar,
    upsert_vectors,
)

logger = logging.getLogger(__name__)


async def store_conversation_summary(
    session: AsyncSession,
    user_id: str,
    conversation_id: UUID,
) -> None:
    """对话结束时将摘要向量化存入 Qdrant"""
    # 获取对话信息
    stmt = select(AIConversation).where(AIConversation.id == conversation_id)
    result = await session.execute(stmt)
    conv = result.scalar_one_or_none()
    if conv is None:
        return

    # 获取摘要内容
    summary_stmt = (
        select(AIMessage)
        .where(
            AIMessage.conversation_id == conversation_id,
            AIMessage.role == "system",
        )
        .order_by(AIMessage.created_at.desc())
        .limit(1)
    )
    summary_result = await session.execute(summary_stmt)
    summary_msg = summary_result.scalar_one_or_none()

    # 构建摘要文本（摘要消息 or 对话标题 + 最后几条消息）
    if summary_msg:
        summary_text = summary_msg.content
    else:
        # 没有摘要时，用标题 + 最后几条消息拼接
        msg_stmt = (
            select(AIMessage)
            .where(AIMessage.conversation_id == conversation_id)
            .order_by(AIMessage.created_at.desc())
            .limit(6)
        )
        msg_result = await session.execute(msg_stmt)
        recent_msgs = list(msg_result.scalars().all())
        parts = [f"对话主题: {conv.title}"]
        for m in reversed(recent_msgs):
            if m.role in ("user", "assistant"):
                parts.append(f"{m.role}: {m.content[:150]}")
        summary_text = "\n".join(parts)

    if not summary_text.strip():
        return

    # 生成嵌入向量
    vector = await get_embedding(session, user_id, summary_text)

    # 存入 Qdrant
    await upsert_vectors(COLLECTION_LONG_TERM_MEMORY, [
        {
            "id": str(conversation_id),
            "vector": vector,
            "payload": {
                "user_id": user_id,
                "conversation_id": str(conversation_id),
                "title": conv.title,
                "summary": summary_text[:500],
                "model_id": conv.model_id,
                "created_at": datetime.utcnow().isoformat(),
            },
        }
    ])
    logger.info(f"对话 {conversation_id} 摘要已存入长期记忆")


async def retrieve_relevant_memories(
    session: AsyncSession,
    user_id: str,
    query: str,
    top_k: int = 3,
) -> list[str]:
    """检索与当前查询相关的历史对话摘要"""
    vector = await get_embedding(session, user_id, query)
    results = await search_similar(
        COLLECTION_LONG_TERM_MEMORY,
        vector,
        limit=top_k,
        user_id_filter=user_id,
    )
    return [r["payload"].get("summary", "") for r in results if r["payload"].get("summary")]


async def store_user_preference(
    session: AsyncSession,
    user_id: str,
    preference: str,
    category: str,
) -> None:
    """存储用户偏好到 Qdrant"""
    vector = await get_embedding(session, user_id, preference)
    await upsert_vectors(COLLECTION_USER_PROFILE, [
        {
            "vector": vector,
            "payload": {
                "user_id": user_id,
                "preference": preference,
                "category": category,
                "created_at": datetime.utcnow().isoformat(),
            },
        }
    ])


# 偏好提取关键词映射
_PREFERENCE_PATTERNS: list[tuple[str, str]] = [
    ("我喜欢", "trading_style"),
    ("我不喜欢", "trading_style"),
    ("偏好", "preference"),
    ("习惯", "preference"),
    ("关注", "focus"),
    ("主要做", "trading_style"),
    ("经常看", "focus"),
    ("重点看", "focus"),
    ("波段", "trading_style"),
    ("日内", "trading_style"),
    ("长线", "trading_style"),
    ("短线", "trading_style"),
]


async def extract_and_store_preferences(
    session: AsyncSession,
    user_id: str,
    user_content: str,
    assistant_content: str,
) -> None:
    """从对话中提取用户偏好并存储"""
    for keyword, category in _PREFERENCE_PATTERNS:
        if keyword in user_content:
            # 取包含关键词的句子作为偏好记录
            sentences = user_content.replace("。", "。\n").replace("！", "！\n").replace("？", "？\n").split("\n")
            for sentence in sentences:
                if keyword in sentence and len(sentence) > 5:
                    await store_user_preference(session, user_id, sentence.strip()[:200], category)
                    break
            break  # 每条消息只提取一次偏好


async def retrieve_user_preferences(
    session: AsyncSession,
    user_id: str,
    query: str,
    top_k: int = 3,
) -> list[str]:
    """检索用户偏好"""
    vector = await get_embedding(session, user_id, query)
    results = await search_similar(
        COLLECTION_USER_PROFILE,
        vector,
        limit=top_k,
        user_id_filter=user_id,
    )
    return [r["payload"].get("preference", "") for r in results if r["payload"].get("preference")]


async def list_all_memories(user_id: str) -> list[dict]:
    """列出用户的所有长期记忆"""
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    from app.services.ai.qdrant import get_qdrant_client

    client = await get_qdrant_client()
    all_items: list[dict] = []
    offset = None

    while True:
        results, next_offset = await client.scroll(
            collection_name=COLLECTION_LONG_TERM_MEMORY,
            scroll_filter=Filter(
                must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]
            ),
            limit=100,
            offset=offset,
            with_payload=True,
        )
        for point in results:
            payload = point.payload or {}
            all_items.append({
                "id": str(point.id),
                "type": "memory",
                "title": payload.get("title", ""),
                "summary": payload.get("summary", ""),
                "created_at": payload.get("created_at", ""),
            })
        if next_offset is None:
            break
        offset = next_offset

    return all_items


async def list_all_preferences(user_id: str) -> list[dict]:
    """列出用户的所有偏好"""
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    from app.services.ai.qdrant import get_qdrant_client

    client = await get_qdrant_client()
    all_items: list[dict] = []
    offset = None

    while True:
        results, next_offset = await client.scroll(
            collection_name=COLLECTION_USER_PROFILE,
            scroll_filter=Filter(
                must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]
            ),
            limit=100,
            offset=offset,
            with_payload=True,
        )
        for point in results:
            payload = point.payload or {}
            all_items.append({
                "id": str(point.id),
                "type": "preference",
                "category": payload.get("category", ""),
                "preference": payload.get("preference", ""),
                "created_at": payload.get("created_at", ""),
            })
        if next_offset is None:
            break
        offset = next_offset

    return all_items


async def delete_memory(memory_id: str) -> bool:
    """删除指定长期记忆"""
    from app.services.ai.qdrant import get_qdrant_client

    client = await get_qdrant_client()
    await client.delete(
        collection_name=COLLECTION_LONG_TERM_MEMORY,
        points_selector=[memory_id],
    )
    return True


async def delete_preference(pref_id: str) -> bool:
    """删除指定用户偏好"""
    from app.services.ai.qdrant import get_qdrant_client

    client = await get_qdrant_client()
    await client.delete(
        collection_name=COLLECTION_USER_PROFILE,
        points_selector=[pref_id],
    )
    return True
