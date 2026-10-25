"""JWT 认证工具模块 —— token 生成/验证、密码哈希"""

from datetime import datetime, timedelta, timezone
from typing import Any

from jose import jwt, JWTError
from passlib.context import CryptContext

from app.core.config import Settings

# bcrypt 密码哈希上下文
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    """对明文密码进行 bcrypt 哈希，返回哈希字符串"""
    return _pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证明文密码与哈希是否匹配"""
    return _pwd_context.verify(plain_password, hashed_password)


def create_access_token(
    settings: Settings,
    user_id: str,
    role: str,
    trading_mode: str = "live",
) -> str:
    """生成 JWT access token，含用户 ID、角色与交易盘模式"""
    from app.core.trading_mode import normalize_trading_mode

    expires_delta = timedelta(minutes=settings.jwt_access_token_expire_minutes)
    expire = datetime.now(timezone.utc) + expires_delta
    payload: dict[str, Any] = {
        "sub": user_id,
        "role": role,
        "trading_mode": normalize_trading_mode(trading_mode),
        "exp": expire,
        "type": "access",
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(
    settings: Settings,
    user_id: str,
    trading_mode: str = "live",
) -> str:
    """生成 JWT refresh token，含用户 ID 与交易盘模式（会话内不可换盘）"""
    from app.core.trading_mode import normalize_trading_mode

    expires_delta = timedelta(days=settings.jwt_refresh_token_expire_days)
    expire = datetime.now(timezone.utc) + expires_delta
    payload: dict[str, Any] = {
        "sub": user_id,
        "trading_mode": normalize_trading_mode(trading_mode),
        "exp": expire,
        "type": "refresh",
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(
    settings: Settings,
    token: str,
) -> dict[str, Any]:
    """解码并验证 JWT token，返回 payload 字典；失败时抛出 JWTError"""
    return jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
    )


def is_token_expired_error(error: JWTError) -> bool:
    """判断 JWTError 是否由 token 过期引起"""
    return "expired" in str(error).lower()
