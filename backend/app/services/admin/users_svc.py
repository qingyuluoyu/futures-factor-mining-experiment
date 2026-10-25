"""管理员用户 CRUD 服务"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import hash_password
from app.models.paper.account import PaperAccount
from app.models.user import User


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def user_to_admin_dict(
    user: User,
    risk_rate: float = 0.0,
    total_equity: float = 0.0,
    total_claimed: float = 0.0,
    live_equity: float = 0.0,
    virtual_equity: float = 0.0,
    live_risk_rate: float = 0.0,
    virtual_risk_rate: float = 0.0,
) -> dict:
    """用户 + 模拟账户摘要 → 管理端列表项（含双盘权益）"""
    return {
        "id": str(user.id),
        "username": user.username,
        "phone": user.phone,
        "email": user.email,
        "avatar": user.avatar,
        "real_name_verified": bool(user.real_name_verified),
        "role": user.role,
        "status": user.status,
        "created_at": _iso(user.created_at) or "",
        "updated_at": _iso(user.updated_at) or "",
        # 兼容旧字段：默认实盘权益
        "risk_rate": risk_rate,
        "total_equity": total_equity,
        "total_claimed": total_claimed,
        "live_equity": live_equity,
        "virtual_equity": virtual_equity,
        "live_risk_rate": live_risk_rate,
        "virtual_risk_rate": virtual_risk_rate,
    }


async def list_users(
    session: AsyncSession,
    keyword: str,
    status_filter: str,
    role_filter: str,
    limit: int,
    offset: int,
) -> dict:
    """分页搜索用户，附带模拟账户风险摘要"""
    safe_limit = max(1, min(limit, 100))
    safe_offset = max(0, offset)

    filters = []
    if keyword:
        like = f"%{keyword.strip()}%"
        filters.append(
            or_(
                User.username.ilike(like),
                User.phone.ilike(like),
                User.email.ilike(like),
            )
        )
    if status_filter in ("active", "frozen"):
        filters.append(User.status == status_filter)
    if role_filter in ("user", "admin"):
        filters.append(User.role == role_filter)

    count_stmt = select(func.count()).select_from(User)
    list_stmt = select(User).order_by(User.created_at.desc())
    for f in filters:
        count_stmt = count_stmt.where(f)
        list_stmt = list_stmt.where(f)

    total = int((await session.execute(count_stmt)).scalar_one())
    list_stmt = list_stmt.offset(safe_offset).limit(safe_limit)
    users = list((await session.execute(list_stmt)).scalars().all())

    # 批量拉取双盘模拟账户
    user_ids = [u.id for u in users]
    live_map: dict[UUID, PaperAccount] = {}
    virt_map: dict[UUID, PaperAccount] = {}
    if user_ids:
        acc_stmt = select(PaperAccount).where(PaperAccount.user_id.in_(user_ids))
        for acc in (await session.execute(acc_stmt)).scalars().all():
            mode = str(getattr(acc, "trading_mode", None) or "live")
            if mode == "virtual":
                virt_map[acc.user_id] = acc
            else:
                live_map[acc.user_id] = acc

    def _equity_risk(acc: PaperAccount | None) -> tuple[float, float, float]:
        if acc is None:
            return 0.0, 0.0, 0.0
        equity = float(acc.balance + acc.frozen_margin)
        claimed = float(acc.total_claimed)
        risk = 0.0
        if equity > 0:
            risk = float(
                (acc.frozen_margin / (acc.balance + acc.frozen_margin) * 100)
            )
        return equity, risk, claimed

    items = []
    for u in users:
        live_eq, live_risk, live_claimed = _equity_risk(live_map.get(u.id))
        virt_eq, virt_risk, _ = _equity_risk(virt_map.get(u.id))
        items.append(
            user_to_admin_dict(
                u,
                risk_rate=live_risk,
                total_equity=live_eq,
                total_claimed=live_claimed,
                live_equity=live_eq,
                virtual_equity=virt_eq,
                live_risk_rate=live_risk,
                virtual_risk_rate=virt_risk,
            )
        )

    return {"total": total, "items": items}


async def get_user_or_404(session: AsyncSession, user_id: UUID) -> User:
    """按 ID 取用户，不存在 404"""
    stmt = select(User).where(User.id == user_id)
    user = (await session.execute(stmt)).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    return user


async def create_user(
    session: AsyncSession,
    username: str,
    password: str,
    phone: str | None,
    email: str | None,
    role: str,
    status_value: str,
) -> User:
    """创建用户（管理员）"""
    exists = (
        await session.execute(select(User).where(User.username == username))
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(status_code=409, detail="用户名已存在")

    if phone:
        p = (
            await session.execute(select(User).where(User.phone == phone))
        ).scalar_one_or_none()
        if p is not None:
            raise HTTPException(status_code=409, detail="手机号已被注册")
    if email:
        e = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if e is not None:
            raise HTTPException(status_code=409, detail="邮箱已被注册")

    user = User(
        username=username,
        phone=phone,
        email=email,
        hashed_password=hash_password(password),
        role=role,
        status=status_value,
    )
    session.add(user)
    await session.flush()
    await session.refresh(user)
    return user


async def update_user(
    session: AsyncSession,
    user_id: UUID,
    phone: str | None,
    email: str | None,
    role: str | None,
    status_value: str | None,
    password: str | None,
    clear_phone: bool,
    clear_email: bool,
    actor_id: UUID,
) -> User:
    """更新用户；禁止自降权限/自冻结导致锁死"""
    user = await get_user_or_404(session, user_id)

    if clear_phone:
        user.phone = None
    elif phone is not None and phone != user.phone:
        p = (
            await session.execute(select(User).where(User.phone == phone))
        ).scalar_one_or_none()
        if p is not None and p.id != user.id:
            raise HTTPException(status_code=409, detail="手机号已被其他用户使用")
        user.phone = phone

    if clear_email:
        user.email = None
    elif email is not None and email != user.email:
        e = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if e is not None and e.id != user.id:
            raise HTTPException(status_code=409, detail="邮箱已被其他用户使用")
        user.email = email

    if role is not None:
        if user.id == actor_id and role != "admin":
            raise HTTPException(status_code=400, detail="不能取消自己的管理员角色")
        user.role = role

    if status_value is not None:
        if user.id == actor_id and status_value == "frozen":
            raise HTTPException(status_code=400, detail="不能冻结自己的账户")
        user.status = status_value

    if password is not None:
        user.hashed_password = hash_password(password)

    await session.flush()
    await session.refresh(user)
    return user


async def delete_user(
    session: AsyncSession,
    user_id: UUID,
    actor_id: UUID,
) -> dict:
    """删除用户（级联清理由 FK ondelete=CASCADE 处理）"""
    if user_id == actor_id:
        raise HTTPException(status_code=400, detail="不能删除自己的账户")
    user = await get_user_or_404(session, user_id)
    await session.delete(user)
    await session.flush()
    return {"message": "用户已删除", "id": str(user_id)}
