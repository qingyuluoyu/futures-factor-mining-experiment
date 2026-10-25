"""FastAPI 依赖注入 —— 获取当前用户、交易盘模式、数据库会话等"""

from dataclasses import dataclass
from typing import Annotated, AsyncGenerator
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import decode_token
from app.core.config import Settings, load_settings
from app.core.trading_mode import DEFAULT_MODE, normalize_trading_mode
from app.models.user import User

# OAuth2 密码模式，token 从 /api/auth/login 获取
_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def get_settings() -> Settings:
    """获取应用配置单例"""
    return load_settings()


async def get_session(
    request: Request,
) -> AsyncGenerator[AsyncSession, None]:
    """从 FastAPI app.state 获取数据库会话（成功时自动提交，异常时回滚）"""
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@dataclass(frozen=True)
class AuthContext:
    """认证上下文：用户 + 会话绑定的交易盘模式"""

    user: User
    trading_mode: str


async def get_auth_context(
    token: Annotated[str, Depends(_oauth2_scheme)],
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> AuthContext:
    """解析 JWT，返回用户与 trading_mode（仅认 token，禁止客户端改盘）"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无效的认证凭据",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = decode_token(settings, token)
        user_id_str: str | None = payload.get("sub")
        token_type: str | None = payload.get("type")
        if user_id_str is None or token_type != "access":
            raise credentials_exception
        trading_mode = normalize_trading_mode(
            str(payload.get("trading_mode") or DEFAULT_MODE)
        )
    except JWTError:
        raise credentials_exception

    user_id = UUID(user_id_str)
    stmt = select(User).where(User.id == user_id)
    result = await session.execute(stmt)
    user: User | None = result.scalar_one_or_none()

    if user is None:
        raise credentials_exception
    if user.status == "frozen":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账户已被冻结",
        )
    return AuthContext(user=user, trading_mode=trading_mode)


async def get_current_user(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> User:
    """从 JWT 返回当前用户（兼容旧依赖）"""
    return ctx.user


async def get_trading_mode(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> str:
    """当前会话交易盘模式 live/virtual"""
    return ctx.trading_mode


async def get_current_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """要求当前用户为管理员，否则 403"""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return current_user


async def require_api_key(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> str:
    """要求 X-API-Key 头有效；命中且启用则更新 last_used_at，返回明文 key。

    用于对外接口（/api/public/*）；与 JWT 认证互不影响。
    """
    from app.services.api_key_svc import authenticate

    raw = request.headers.get("X-API-Key", "").strip()
    record = await authenticate(session, raw)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效或已停用的 API Key",
            headers={"WWW-Authenticate": 'ApiKey realm="api-key"'},
        )
    request.state.api_key = record
    return raw
