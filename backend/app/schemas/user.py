"""用户信息管理相关的 Pydantic schema"""

from typing import Annotated

from pydantic import BaseModel, Field, field_validator


class UpdateUserRequest(BaseModel):
    """更新用户信息请求体"""

    phone: Annotated[str | None, Field(max_length=20, description="手机号")] = None
    email: Annotated[str | None, Field(max_length=100, description="邮箱")] = None
    avatar: Annotated[str | None, Field(max_length=500, description="头像 URL")] = None

    @field_validator("email")
    @classmethod
    def email_format(cls, value: str | None) -> str | None:
        """简单校验邮箱格式"""
        if value is not None and "@" not in value:
            raise ValueError("邮箱格式不正确")
        return value


class ChangePasswordRequest(BaseModel):
    """修改密码请求体"""

    old_password: Annotated[str, Field(min_length=6, max_length=100, description="旧密码")]
    new_password: Annotated[str, Field(min_length=6, max_length=100, description="新密码")]
