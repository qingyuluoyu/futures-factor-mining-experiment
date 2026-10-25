"""AI 渠道商 Schema —— 请求/响应 Pydantic 模型"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ProviderCreate(BaseModel):
    """创建渠道请求"""
    name: str = Field(..., min_length=1, max_length=100, description="渠道名称")
    api_type: str = Field(..., pattern=r"^(openai|claude|jev)$", description="接口规范")
    base_url: str = Field(..., min_length=1, max_length=500, description="API 基础地址")
    api_key: str = Field(..., min_length=1, max_length=500, description="API Key")
    thinking_enabled: bool | None = Field(
        default=None,
        description="思考模式：None=跟随模型自动，True=强制开启，False=强制关闭",
    )


class ProviderUpdate(BaseModel):
    """更新渠道请求 —— 字段均可选；api_key 为空/省略表示不改动"""

    name: str | None = Field(default=None, min_length=1, max_length=100, description="渠道名称")
    api_type: str | None = Field(
        default=None,
        pattern=r"^(openai|claude|jev)$",
        description="接口规范",
    )
    base_url: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
        description="API 基础地址",
    )
    api_key: str | None = Field(
        default=None,
        max_length=500,
        description="API Key，留空则保留原密钥",
    )
    is_active: bool | None = Field(default=None, description="是否启用")
    thinking_enabled: bool | None = Field(
        default=None,
        description="思考模式；显式传 null 表示恢复「跟随模型自动」",
    )


class ProviderResponse(BaseModel):
    """渠道响应（API Key 脱敏）"""
    id: UUID
    user_id: UUID
    name: str
    api_type: str
    base_url: str
    api_key_masked: str = Field(alias="api_key", description="脱敏后的 API Key")
    is_active: bool
    thinking_enabled: bool | None = Field(
        default=None, description="思考模式：None=自动，True=开，False=关"
    )
    created_at: datetime
    model_count: int = Field(default=0, description="该渠道下模型数量")

    model_config = {"from_attributes": True, "populate_by_name": True}


class ProviderTestResult(BaseModel):
    """渠道测试结果"""
    status: str = Field(description="ok 或 error")
    message: str = Field(default="", description="错误信息或成功提示")
    models: list[str] = Field(default_factory=list, description="可用模型 ID 列表")


class PresetProvider(BaseModel):
    """预设渠道配置"""
    name: str
    api_type: str
    base_url: str
    description: str = ""
