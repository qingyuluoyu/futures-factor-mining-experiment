"""AI 对话历史 CRUD API"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_session
from app.core.financial_request_route import FinancialRequestRoute
from app.models.ai_conversation import AIConversation, AIMessage
from app.models.user import User
from app.schemas.ai_conversation import (
    ConversationCreate,
    ConversationItem,
    MessageItem,
    MessagePage,
)

def private_response(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/api/ai/conversations", tags=["AI 对话"], route_class=FinancialRequestRoute, dependencies=[Depends(private_response)])


@router.post("", response_model=ConversationItem)
async def create_conversation(
    body: ConversationCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ConversationItem:
    """新建空对话"""
    conv = AIConversation(
        user_id=current_user.id,
        model_id=body.model_id,
    )
    session.add(conv)
    await session.flush()
    await session.refresh(conv)
    return ConversationItem.model_validate(conv)


@router.get("", response_model=list[ConversationItem])
async def list_conversations(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10000),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[ConversationItem]:
    """获取对话列表，按更新时间倒序"""
    stmt = (
        select(AIConversation)
        .where(AIConversation.user_id == current_user.id)
        .order_by(AIConversation.updated_at.desc(), AIConversation.id.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await session.execute(stmt)
    convs = result.scalars().all()
    return [ConversationItem.model_validate(c) for c in convs]


@router.delete("/{conversation_id}")
async def delete_conversation(
    conversation_id: UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict:
    """删除对话及其所有消息（级联删除）"""
    stmt = select(AIConversation).where(
        AIConversation.id == conversation_id,
        AIConversation.user_id == current_user.id,
    )
    result = await session.execute(stmt)
    conv: AIConversation | None = result.scalar_one_or_none()
    if conv is None:
        raise HTTPException(status_code=404, detail="对话不存在")
    await session.delete(conv)
    return {"ok": True}


@router.get("/{conversation_id}/messages", response_model=MessagePage)
async def get_messages(
    conversation_id: UUID,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> MessagePage:
    """获取对话消息（分页，按时间正序）"""
    # 校验对话归属
    conv_stmt = select(AIConversation).where(
        AIConversation.id == conversation_id,
        AIConversation.user_id == current_user.id,
    )
    conv_result = await session.execute(conv_stmt)
    if conv_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="对话不存在")

    # 总数
    count_stmt = select(func.count()).select_from(AIMessage).where(
        AIMessage.conversation_id == conversation_id
    )
    total = (await session.execute(count_stmt)).scalar_one()

    # 分页查询（时间正序，取最后 size 条）
    end = max(0, total - (page - 1) * size)
    offset = max(0, end - size)
    msg_stmt = (
        select(AIMessage)
        .where(AIMessage.conversation_id == conversation_id)
        .order_by(AIMessage.created_at.asc(), AIMessage.id.asc())
        .offset(offset)
        .limit(end - offset)
    )
    result = await session.execute(msg_stmt)
    messages = result.scalars().all()

    return MessagePage(
        items=[MessageItem.model_validate(m) for m in messages],
        total=total,
        page=page,
        size=size,
    )


async def auto_rename_conversation(
    session: AsyncSession,
    conversation_id: UUID,
    content: str,
) -> None:
    """取消息前 20 字符作为对话标题"""
    title = content.strip()[:20] or "新对话"
    stmt = (
        update(AIConversation)
        .where(AIConversation.id == conversation_id)
        .values(title=title)
    )
    await session.execute(stmt)


async def save_message(
    session: AsyncSession,
    conversation_id: UUID,
    role: str,
    content: str,
    tool_calls: list[dict] | dict | None = None,
    tool_result: list[dict] | dict | None = None,
) -> AIMessage:
    """保存一条消息到数据库"""
    msg = AIMessage(
        conversation_id=conversation_id,
        role=role,
        content=content,
        tool_calls=tool_calls,
        tool_result=tool_result,
    )
    session.add(msg)
    await session.flush()
    return msg
