"""AI 模型 Schema —— 请求/响应 Pydantic 模型"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ModelCreate(BaseModel):
    """创建模型请求"""
    model_id: str = Field(..., min_length=1, max_length=200, description="模型标识")
    display_name: str = Field(..., min_length=1, max_length=200, description="显示名称")
    is_multimodal: bool = Field(default=False, description="是否支持多模态")
    is_custom: bool = Field(default=False, description="是否自定义模型")
    capabilities: list[str] = Field(default_factory=list, description="能力标签")


class ModelBatchCreate(BaseModel):
    """从渠道批量添加模型请求"""
    models: list[ModelCreate] = Field(..., min_length=1, description="模型列表")


class ModelUpdate(BaseModel):
    """更新模型请求"""
    display_name: str = Field(..., min_length=1, max_length=200, description="显示名称")


class ModelResponse(BaseModel):
    """模型响应"""
    id: UUID
    user_id: UUID
    provider_id: UUID
    model_id: str
    display_name: str
    is_multimodal: bool
    is_custom: bool
    capabilities: list[str] | None
    created_at: datetime
    # 关联信息
    provider_name: str = Field(default="", description="所属渠道名称")
    provider_api_type: str = Field(default="", description="渠道接口规范")

    model_config = {"from_attributes": True}
