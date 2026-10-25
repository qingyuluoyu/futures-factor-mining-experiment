"""认证 API 路由 —— 登录、注册、刷新 token、获取当前用户"""

import logging
from time import perf_counter
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.core.config import Settings
from app.core.deps import (
    AuthContext,
    get_auth_context,
    get_current_user,
    get_session,
    get_settings,
)
from app.core.trading_mode import normalize_trading_mode
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    RegistrationStatusResponse,
    TokenResponse,
    UserInfoResponse,
)
from app.services.registration_svc import (
    assert_can_register,
    get_public_registration_status,
)

router = APIRouter(prefix="/api/auth", tags=["认证"])
logger = logging.getLogger("qihuo.auth")


@router.get("/registration-status", response_model=RegistrationStatusResponse)
async def registration_status(
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    """公开：是否开放注册、剩余名额、重置时间"""
    return await get_public_registration_status(session, settings)


async def _register_user(
    session: AsyncSession,
    settings: Settings,
    request_body: RegisterRequest,
    request_id: str | None = None,
) -> TokenResponse:
    """注册新用户并返回 token（纯逻辑，供路由调用）"""
    await assert_can_register(session, settings)

    # 检查用户名是否已存在
    stmt = select(User).where(User.username == request_body.username)
    result = await session.execute(stmt)
    if result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="用户名已存在",
        )

    # 检查手机号是否已被使用
    if request_body.phone is not None:
        stmt_phone = select(User).where(User.phone == request_body.phone)
        result_phone = await session.execute(stmt_phone)
        if result_phone.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="手机号已被注册",
            )

    # 检查邮箱是否已被使用
    if request_body.email is not None:
        stmt_email = select(User).where(User.email == request_body.email)
        result_email = await session.execute(stmt_email)
        if result_email.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="邮箱已被注册",
            )

    # 创建用户
    hashed = hash_password(request_body.password)
    new_user = User(
        username=request_body.username,
        phone=request_body.phone,
        email=request_body.email,
        hashed_password=hashed,
    )
    session.add(new_user)
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        if getattr(error.orig, "sqlstate", None) == "23505":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="用户名、手机号或邮箱已被注册",
            ) from None
        raise
    await session.refresh(new_user)
    logger.info(
        "request_id=%s user_id=%s operation=register_persisted registration_policy=%s",
        request_id,
        new_user.id,
        settings.registration_policy,
    )

    mode = normalize_trading_mode(request_body.trading_mode)
    access_token = create_access_token(
        settings, str(new_user.id), new_user.role, mode
    )
    refresh_token = create_refresh_token(settings, str(new_user.id), mode)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
    )


async def _login_user(
    session: AsyncSession,
    settings: Settings,
    request_body: LoginRequest,
) -> TokenResponse:
    """用户登录并返回 token（纯逻辑，供路由调用）"""
    stmt = select(User).where(User.username == request_body.username)
    result = await session.execute(stmt)
    user: User | None = result.scalar_one_or_none()

    if user is None or not verify_password(request_body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )

    if user.status == "frozen":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账户已被冻结",
        )

    mode = normalize_trading_mode(request_body.trading_mode)
    access_token = create_access_token(
        settings, str(user.id), user.role, mode
    )
    refresh_token = create_refresh_token(settings, str(user.id), mode)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    """注册新用户，成功后返回 access_token 和 refresh_token"""
    request_id = uuid4().hex
    started = perf_counter()
    response.headers["X-Request-ID"] = request_id
    try:
        result = await _register_user(session, settings, body, request_id=request_id)
    except HTTPException as error:
        logger.warning(
            "request_id=%s operation=register status_code=%s elapsed_ms=%.1f",
            request_id,
            error.status_code,
            (perf_counter() - started) * 1000,
        )
        raise HTTPException(
            status_code=error.status_code,
            detail=error.detail,
            headers={**(error.headers or {}), "X-Request-ID": request_id},
        ) from None
    except Exception as error:
        await session.rollback()
        logger.error(
            "request_id=%s operation=register error_type=%s elapsed_ms=%.1f",
            request_id,
            type(error).__name__,
            (perf_counter() - started) * 1000,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="注册处理失败，请稍后核对账号状态",
            headers={"X-Request-ID": request_id},
        ) from None
    logger.info(
        "request_id=%s operation=register status_code=201 elapsed_ms=%.1f",
        request_id,
        (perf_counter() - started) * 1000,
    )
    return result


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    """用户登录，验证凭据后返回 token"""
    return await _login_user(session, settings, body)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    body: RefreshRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    """使用 refresh token 换取新的 access token"""
    from jose import JWTError

    try:
        payload = decode_token(settings, body.refresh_token)
        token_type: str | None = payload.get("type")
        user_id_str: str | None = payload.get("sub")
        if token_type != "refresh" or user_id_str is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="无效的 refresh token",
            )
        # 会话绑定：优先 JWT 内 mode，禁止用 body 改盘
        mode = normalize_trading_mode(
            str(payload.get("trading_mode") or body.trading_mode or "live")
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="refresh token 已过期或无效",
        )

    # 验证用户仍然存在且未冻结
    from uuid import UUID

    stmt = select(User).where(User.id == UUID(user_id_str))
    result = await session.execute(stmt)
    user: User | None = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在",
        )
    if user.status == "frozen":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账户已被冻结",
        )

    new_access = create_access_token(settings, str(user.id), user.role, mode)
    new_refresh = create_refresh_token(settings, str(user.id), mode)
    return TokenResponse(access_token=new_access, refresh_token=new_refresh)


@router.get("/me", response_model=UserInfoResponse)
async def get_me(
    ctx: AuthContext = Depends(get_auth_context),
) -> UserInfoResponse:
    """获取当前登录用户信息（含会话 trading_mode）"""
    data = UserInfoResponse.model_validate(ctx.user)
    return data.model_copy(update={"trading_mode": ctx.trading_mode})
