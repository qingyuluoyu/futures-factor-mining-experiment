"""AI Skill 相关的 Pydantic schema —— 请求/响应数据结构"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field


# ============ 商城相关 Schema ============


class MarketplaceSkillItem(BaseModel):
    """商城 skill 列表项"""

    id: str = Field(description="商城 skill ID")
    name: str = Field(description="Skill 名称")
    author: str = Field(description="作者")
    description: str = Field(description="描述")
    install_count: int = Field(default=0, description="安装数")
    trigger_word: str = Field(description="触发词")
    git_url: str = Field(default="", description="GitHub 仓库地址")


class MarketplaceSkillDetail(BaseModel):
    """商城 skill 详情"""

    id: str = Field(description="商城 skill ID")
    name: str = Field(description="Skill 名称")
    author: str = Field(description="作者")
    description: str = Field(description="完整描述")
    skill_md_preview: str = Field(default="", description="SKILL.md 预览内容")
    install_count: int = Field(default=0, description="安装数")
    trigger_word: str = Field(description="触发词")
    git_url: str = Field(default="", description="GitHub 仓库地址")


# ============ 已安装 Skill Schema ============


class InstalledSkillItem(BaseModel):
    """已安装 skill 列表项"""

    id: UUID = Field(description="Skill 唯一标识")
    name: str = Field(description="Skill 名称")
    trigger_word: str = Field(description="触发词")
    source: str = Field(description="来源：marketplace/upload/git_url")
    source_url: str = Field(description="来源 URL")
    description: str = Field(description="描述")
    is_enabled: bool = Field(description="是否启用")
    created_at: datetime = Field(description="安装时间")

    model_config = {"from_attributes": True}


# ============ 请求 Schema ============


class SkillInstallRequest(BaseModel):
    """安装 skill 请求"""

    marketplace_id: str | None = Field(default=None, description="商城 skill ID")
    git_url: str | None = Field(default=None, description="GitHub 仓库地址")


class SkillUploadRequest(BaseModel):
    """上传 skill 请求（元数据部分）"""

    name: str = Field(description="Skill 名称")
    trigger_word: str = Field(description="触发词")
    description: str = Field(default="", description="描述")


# ============ Skill 配置 Schema ============


class SkillConfigResponse(BaseModel):
    """Skill 配置响应"""

    has_token: bool = Field(description="是否已配置 token")
    masked_token: str = Field(default="", description="脱敏后的 token")


class SkillConfigUpdateRequest(BaseModel):
    """更新 Skill 配置请求"""

    token: str = Field(description="skills.sh API token")
