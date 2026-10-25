"""用户信息管理 API 路由 —— 更新个人信息、修改密码"""

import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.core.auth import hash_password, verify_password
from app.core.deps import AuthContext, get_auth_context, get_current_user, get_session
from app.models.user import User
from app.schemas.auth import UserInfoResponse
from app.schemas.user import ChangePasswordRequest, UpdateUserRequest
from app.schemas.notify import NotifySettingResponse, NotifySettingUpdate

router = APIRouter(prefix="/api/users", tags=["用户管理"])
logger = logging.getLogger("qihuo.users")


@router.get("/me/market")
async def get_market_type(
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    """当前市场偏好（futures=期货 / stock=A股）"""
    return {"market_type": current_user.market_type or "futures"}


@router.put("/me/market")
async def switch_market_type(
    body: dict,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """切换市场偏好（futures=期货 / stock=A股），持久化到用户"""
    market = str(body.get("market_type") or "").strip().lower()
    if market not in ("futures", "stock"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="market_type 仅支持 futures / stock",
        )
    current_user.market_type = market
    await session.commit()
    return {"market_type": market}


@router.put("/me", response_model=UserInfoResponse)
async def update_user_info(
    body: UpdateUserRequest,
    request: Request,
    response: Response,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> UserInfoResponse:
    """更新当前用户的基本信息（手机号、邮箱、头像）"""
    from sqlalchemy import select

    current_user = ctx.user
    user_id = current_user.id
    request_id = str(getattr(request.state, "request_id", None) or uuid4().hex)
    headers = {"X-Request-ID": request_id, "Cache-Control": "no-store"}
    response.headers.update(headers)

    # 检查手机号唯一性
    if body.phone is not None and body.phone != current_user.phone:
        stmt = select(User).where(User.phone == body.phone)
        result = await session.execute(stmt)
        if result.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="手机号已被其他用户使用",
                headers=headers,
            )

    # 检查邮箱唯一性
    if body.email is not None and body.email != current_user.email:
        stmt = select(User).where(User.email == body.email)
        result = await session.execute(stmt)
        if result.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="邮箱已被其他用户使用",
                headers=headers,
            )

    if body.phone is not None:
        current_user.phone = body.phone
    if body.email is not None:
        current_user.email = body.email

    if body.avatar is not None:
        current_user.avatar = body.avatar

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        logger.warning("request_id=%s user_id=%s operation=profile_update error=contact_conflict", request_id, user_id)
        raise HTTPException(status_code=409, detail="手机号或邮箱已被其他用户使用", headers=headers) from None
    await session.refresh(current_user)
    logger.info("request_id=%s user_id=%s operation=profile_update trading_mode=%s", request_id, user_id, ctx.trading_mode)
    return UserInfoResponse.model_validate(current_user).model_copy(update={"trading_mode": ctx.trading_mode})


@router.put("/me/password")
async def change_password(
    body: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """修改当前用户密码"""
    if not verify_password(body.old_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="旧密码不正确",
        )

    current_user.hashed_password = hash_password(body.new_password)
    await session.commit()
    return {"message": "密码修改成功"}


@router.get("/me/notify-settings", response_model=NotifySettingResponse)
async def get_notify_settings(
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> NotifySettingResponse:
    """读取当前用户通知开关（首次读取自动建默认行）"""
    from app.services.notify.preferences import get_or_create_preferences

    response.headers["Cache-Control"] = "no-store"
    request_id = str(getattr(request.state, "request_id", None) or uuid4().hex)
    response.headers["X-Request-ID"] = request_id
    setting = await get_or_create_preferences(session, current_user.id)
    await session.commit()
    await session.refresh(setting)
    logger.info("request_id=%s user_id=%s operation=notify_settings_read", request_id, current_user.id)
    return NotifySettingResponse.model_validate(setting)


@router.put("/me/notify-settings", response_model=NotifySettingResponse)
async def update_notify_settings(
    body: NotifySettingUpdate,
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> NotifySettingResponse:
    """更新当前用户通知开关（含提示音，可选）"""
    from app.services.notify.preferences import get_or_create_preferences

    response.headers["Cache-Control"] = "no-store"
    request_id = str(getattr(request.state, "request_id", None) or uuid4().hex)
    response.headers["X-Request-ID"] = request_id
    setting = await get_or_create_preferences(session, current_user.id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(setting, field, value)
    await session.commit()
    await session.refresh(setting)
    logger.info("request_id=%s user_id=%s operation=notify_settings_update fields=%s", request_id, current_user.id, sorted(body.model_fields_set))
    return NotifySettingResponse.model_validate(setting)
