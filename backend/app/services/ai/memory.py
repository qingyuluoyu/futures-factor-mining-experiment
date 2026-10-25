"""AI 记忆服务 —— 短期记忆（滑动窗口 + 摘要压缩）"""

import json
import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_conversation import AIConversation, AIMessage

logger = logging.getLogger(__name__)

# 滑动窗口保留的最大轮数
MAX_CONTEXT_ROUNDS = 20
# 触发摘要的轮数阈值
SUMMARY_THRESHOLD = 20
# 每次摘要压缩的轮数范围
SUMMARY_BATCH_SIZE = 10


async def load_context_messages(
    session: AsyncSession,
    conversation_id: UUID,
) -> list[dict]:
    """加载对话上下文消息（应用滑动窗口 + 摘要）"""
    # 查询所有消息（按时间正序）
    stmt = (
        select(AIMessage)
        .where(AIMessage.conversation_id == conversation_id)
        .order_by(AIMessage.created_at.asc())
    )
    result = await session.execute(stmt)
    all_messages = list(result.scalars().all())

    if not all_messages:
        return []

    # 统计轮数（user + assistant 配对）
    rounds = _count_rounds(all_messages)

    # 触发异步摘要（不阻塞当前请求）
    if rounds >= SUMMARY_THRESHOLD:
        await _maybe_summarize(session, conversation_id, all_messages)
        # 重新加载以获取摘要
        result = await session.execute(stmt)
        all_messages = list(result.scalars().all())

    # 构建上下文：摘要（如有）+ 最近 20 轮
    return _build_context(all_messages)


def _count_rounds(messages: list[AIMessage]) -> int:
    """统计有效对话轮数（user/assistant 配对）"""
    rounds = 0
    for msg in messages:
        if msg.role == "user":
            rounds += 1
    return rounds


def _build_context(messages: list[AIMessage]) -> list[dict]:
    """从消息列表构建 AI 上下文（摘要 + 滑动窗口）"""
    # 提取摘要消息
    summaries = [m for m in messages if m.role == "system" and _is_summary(m)]

    # 非摘要消息
    regular = [m for m in messages if not (_is_summary(m))]

    # 取最近 20 轮（20 个 user 消息及其后的 assistant 消息）
    recent = _take_recent_rounds(regular, MAX_CONTEXT_ROUNDS)

    # 组合：摘要 + 最近轮次
    context: list[dict] = []
    for s in summaries:
        context.append({"role": "system", "content": s.content})
    for m in recent:
        content = m.content
        if m.role == "tool":
            continue
        # 工作记忆：包含工具调用信息
        if m.role == "assistant" and m.tool_calls:
            tool_info = "; ".join(
                f"{tc.get('name', '?')}({tc.get('args', '')})" for tc in m.tool_calls
            )
            if tool_info and content:
                content = f"{content}\n[已执行工具: {tool_info}]"
            elif tool_info:
                content = f"[已执行工具: {tool_info}]"
        context.append({"role": m.role, "content": content})

    return context


def _take_recent_rounds(messages: list[AIMessage], max_rounds: int) -> list[AIMessage]:
    """从消息列表尾部取最近 N 轮"""
    # 找到 user 消息的索引
    user_indices = [i for i, m in enumerate(messages) if m.role == "user"]
    if not user_indices:
        return messages[-2:] if len(messages) > 2 else messages

    # 取最后 max_rounds 个 user 消息的起始位置
    start_idx = user_indices[-max_rounds] if len(user_indices) >= max_rounds else 0
    return messages[start_idx:]


def _is_summary(msg: AIMessage) -> bool:
    """判断消息是否为摘要"""
    if msg.role != "system":
        return False
    # 通过 tool_result 字段存储元数据标记摘要
    if msg.tool_result and isinstance(msg.tool_result, dict):
        return msg.tool_result.get("type") == "summary"
    return False


async def _maybe_summarize(
    session: AsyncSession,
    conversation_id: UUID,
    messages: list[AIMessage],
) -> None:
    """检查并生成摘要（第 1-10 轮压缩为一条摘要）"""
    # 检查是否已有摘要
    existing_summary = any(_is_summary(m) for m in messages)
    if existing_summary:
        return

    # 提取前 10 轮（10 个 user + 对应 assistant）
    early_messages = _take_first_rounds(messages, SUMMARY_BATCH_SIZE)
    if len(early_messages) < 4:
        # 太少不值得摘要
        return

    # 生成摘要文本
    conversation_text = _format_for_summary(early_messages)
    summary_text = await _generate_summary(session, conversation_id, conversation_text)

    if not summary_text:
        return

    # 存储摘要消息
    summary_msg = AIMessage(
        conversation_id=conversation_id,
        role="system",
        content=f"[对话摘要] {summary_text}",
        tool_result={"type": "summary", "rounds_summarized": SUMMARY_BATCH_SIZE},
    )
    session.add(summary_msg)
    await session.flush()
    logger.info(f"对话 {conversation_id} 生成摘要完成")


def _take_first_rounds(messages: list[AIMessage], count: int) -> list[AIMessage]:
    """取前 N 轮消息"""
    user_count = 0
    end_idx = len(messages)
    for i, m in enumerate(messages):
        if m.role == "user":
            user_count += 1
            if user_count >= count:
                # 包含该 user 后面的 assistant 消息
                end_idx = i + 1
                while end_idx < len(messages) and messages[end_idx].role not in ("user",):
                    end_idx += 1
                break
    return messages[:end_idx]


def _format_for_summary(messages: list[AIMessage]) -> str:
    """格式化消息用于摘要生成"""
    lines: list[str] = []
    for m in messages:
        if m.role in ("user", "assistant"):
            role_label = "用户" if m.role == "user" else "助手"
            lines.append(f"{role_label}: {m.content[:200]}")
    return "\n".join(lines)


async def _generate_summary(
    session: AsyncSession,
    conversation_id: UUID,
    conversation_text: str,
) -> str:
    """调用 AI 生成对话摘要"""
    try:
        # 获取对话使用的模型
        stmt = select(AIConversation.model_id).where(AIConversation.id == conversation_id)
        result = await session.execute(stmt)
        model_id = result.scalar_one_or_none()
        if not model_id:
            return ""

        # 使用 chat_stream 生成摘要
        summary_prompt = [
            {"role": "system", "content": "你是一个对话摘要助手。请将以下对话内容压缩为一段简洁的中文摘要（100字以内），保留关键信息和上下文。"},
            {"role": "user", "content": f"请总结以下对话：\n\n{conversation_text}"},
        ]

        # 通过 gateway 调用 AI
        from app.services.ai.gateway import chat_stream as ai_chat_stream
        # 获取对话所属用户
        stmt2 = select(AIConversation.user_id).where(AIConversation.id == conversation_id)
        result2 = await session.execute(stmt2)
        user_id = result2.scalar_one_or_none()
        if not user_id:
            return ""

        full_text = ""
        async for event in ai_chat_stream(session, str(user_id), model_id, summary_prompt, tools_enabled=False):
            if event.type == "message":
                full_text += event.content
            elif event.type == "error":
                logger.warning("conversation_id=%s error_code=summary_generation_failed", conversation_id)
                return ""

        return full_text.strip()
    except Exception as error:
        logger.warning("conversation_id=%s operation=generate_summary error_type=%s", conversation_id, type(error).__name__)
        return ""
