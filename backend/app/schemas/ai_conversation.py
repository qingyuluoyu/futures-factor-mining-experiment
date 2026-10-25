"""AI 对话相关 Schema —— 请求/响应模型"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


# ---------- 对话 ----------

class ConversationCreate(BaseModel):
    """创建对话请求"""
    model_id: str = Field(default="", description="初始模型标识")


class ConversationItem(BaseModel):
    """对话列表项"""
    id: UUID
    title: str
    model_id: str
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------- 消息 ----------

class MessageItem(BaseModel):
    """消息项"""
    id: UUID
    conversation_id: UUID
    role: str
    content: str
    tool_calls: list[dict] | dict | None = None
    tool_result: list[dict] | dict | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class MessagePage(BaseModel):
    """消息分页结果"""
    items: list[MessageItem]
    total: int
    page: int
    size: int


# ---------- 聊天请求（扩展版，携带对话上下文） ----------

class ChatWithContextRequest(BaseModel):
    """带对话上下文的聊天请求"""
    model_id: str = Field(..., description="模型标识")
    conversation_id: UUID | None = Field(default=None, description="对话 ID（新建传 None）")
    content: str = Field(..., min_length=1, description="用户消息内容")
    images: list[str] = Field(default_factory=list, description="图片 URL 列表（多模态）")
