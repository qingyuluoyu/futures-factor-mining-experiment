"""模拟账户服务 —— 开户、月度领取、流水查询"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.paper.account import PaperAccount
from app.models.paper.ledger import PaperLedger
from app.models.paper.position import PaperPosition
from app.services.notify.trade_notify import notify_fund_change
from app.services.paper.constants import CLAIM_TZ_NAME, MONTHLY_CLAIM_AMOUNT
from app.services.system_settings_svc import (
    current_claim_period_key,
    ensure_system_settings,
)


def _now_utc() -> datetime:
    """当前 UTC 时间（带时区）"""
    return datetime.now(timezone.utc)


def _iso_utc(dt: datetime | None) -> str | None:
    """统一 UTC ISO（带 Z），供前端转上海时间"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def current_claim_month() -> str:
    """当前自然月（上海时区）YYYY-MM —— 兼容旧逻辑"""
    now = datetime.now(ZoneInfo(CLAIM_TZ_NAME))
    return f"{now.year:04d}-{now.month:02d}"


async def ensure_paper_account(
    session: AsyncSession,
    user_id: UUID,
    trading_mode: str = "live",
) -> PaperAccount:
    """获取或创建用户在指定盘下的模拟账户（惰性开户）"""
    from app.core.trading_mode import normalize_trading_mode

    mode = normalize_trading_mode(trading_mode)
    if session.get_bind().dialect.name == "postgresql":
        lock_key = int.from_bytes(
            sha256(f"paper-custom-funds:{user_id}:{mode}".encode()).digest()[:8],
            "big",
            signed=True,
        )
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        )
    elif session.get_bind().dialect.name == "sqlite":
        await session.execute(
            update(PaperAccount).where(
                PaperAccount.user_id == user_id,
                PaperAccount.trading_mode == mode,
            ).values(balance=PaperAccount.balance).execution_options(synchronize_session=False)
        )
    stmt = select(PaperAccount).where(
        PaperAccount.user_id == user_id,
        PaperAccount.trading_mode == mode,
    ).with_for_update().execution_options(populate_existing=True)
    result = await session.execute(stmt)
    account = result.scalar_one_or_none()
    if account is not None:
        return account

    account = PaperAccount(
        user_id=user_id,
        trading_mode=mode,
        balance=Decimal("0"),
        frozen_margin=Decimal("0"),
        realized_pnl=Decimal("0"),
        total_claimed=Decimal("0"),
        last_claim_month=None,
        currency="CNY",
        created_at=_now_utc(),
        updated_at=_now_utc(),
    )
    session.add(account)
    await session.flush()
    return account


def _append_ledger(
    session: AsyncSession,
    account: PaperAccount,
    flow_type: str,
    amount: Decimal,
    description: str,
    ref_type: str | None,
    ref_id: UUID | None,
) -> PaperLedger:
    """写入一条资金流水（不 commit）"""
    ledger = PaperLedger(
        account_id=account.id,
        user_id=account.user_id,
        flow_type=flow_type,
        amount=amount,
        balance_after=account.balance,
        description=description,
        ref_type=ref_type,
        ref_id=ref_id,
        created_at=_now_utc(),
    )
    session.add(ledger)
    return ledger


async def deposit_custom_funds(
    session: AsyncSession,
    user_id: UUID,
    amount: Decimal,
    idempotency_key: UUID,
    trading_mode: str = "live",
) -> dict:
    from app.core.trading_mode import normalize_trading_mode

    mode = normalize_trading_mode(trading_mode)
    maximum = Decimal("9999999999999999.99")
    if not amount.is_finite() or amount <= 0 or amount > maximum or amount.as_tuple().exponent < -2:
        raise HTTPException(status_code=422, detail="充值金额必须为可存储的正数，最多两位小数")
    dialect = session.get_bind().dialect.name
    if dialect not in ("sqlite", "postgresql"):
        raise HTTPException(status_code=503, detail="当前数据库尚未启用安全的模拟充值")
    account = await ensure_paper_account(session, user_id, mode)
    previous = (await session.execute(select(PaperLedger).where(
        PaperLedger.account_id == account.id,
        PaperLedger.ref_type == "custom_deposit",
        PaperLedger.ref_id == idempotency_key,
    ))).scalar_one_or_none()
    if previous is not None:
        if previous.amount != amount:
            raise HTTPException(status_code=409, detail="此充值幂等键已用于其他金额")
        result = await get_account_summary(session, user_id, mode)
        result.update(deposit_id=str(previous.id), deposit_amount=format(previous.amount, ".2f"), idempotency_key=str(idempotency_key), replayed=True)
        return result
    if account.balance + amount > maximum:
        raise HTTPException(status_code=409, detail="充值后余额超出数据库金额存储范围")
    account.balance += amount
    account.updated_at = _now_utc()
    ledger = _append_ledger(session, account, "deposit", amount, "自定义模拟资金充值（仅本地模拟）", "custom_deposit", idempotency_key)
    await session.flush()
    result = await get_account_summary(session, user_id, mode)
    result.update(deposit_id=str(ledger.id), deposit_amount=format(amount, ".2f"), idempotency_key=str(idempotency_key), replayed=False)
    return result


async def claim_monthly_funds(
    session: AsyncSession,
    user_id: UUID,
    trading_mode: str = "live",
) -> dict:
    """按系统设置领取虚拟练习金；周期内已领或超总额则 409"""
    account = await ensure_paper_account(session, user_id, trading_mode)
    settings = await ensure_system_settings(session)
    period_key = current_claim_period_key(
        settings.paper_claim_period,
        settings.paper_claim_reset_hour,
    )
    amount = Decimal(str(settings.paper_claim_amount))
    if amount <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="系统未配置可领取练手资金",
        )

    if account.last_claim_month == period_key:
        label = "本周期" if settings.paper_claim_period == "daily" else "本月"
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{label}（{period_key}）已领取虚拟练习金，请等待下一周期",
        )

    cap = Decimal(str(settings.paper_total_claim_cap))
    if cap > 0 and (account.total_claimed + amount) > cap:
        remaining = cap - account.total_claimed
        if remaining <= 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"已达可领取练手资金总额上限 ¥{cap}",
            )
        # 最后一笔按剩余额度领取
        amount = remaining

    account.balance = account.balance + amount
    account.total_claimed = account.total_claimed + amount
    account.last_claim_month = period_key
    account.updated_at = _now_utc()

    _append_ledger(
        session,
        account,
        "claim",
        amount,
        f"领取 {period_key} 虚拟练习金 ¥{amount}",
        None,
        None,
    )
    await session.flush()
    await notify_fund_change(
        session,
        account.user_id,
        account.trading_mode,
        amount,
        f"领取 {period_key} 虚拟练习金",
    )
    return await get_account_summary(session, user_id, trading_mode)


async def _sum_position_margin(
    session: AsyncSession,
    account_id: UUID,
) -> Decimal:
    """汇总持仓占用保证金"""
    stmt = select(PaperPosition).where(
        PaperPosition.account_id == account_id,
        PaperPosition.quantity > 0,
    )
    result = await session.execute(stmt)
    positions = list(result.scalars().all())
    total = Decimal("0")
    for pos in positions:
        total += pos.margin
    return total


async def get_account_summary(
    session: AsyncSession,
    user_id: UUID,
    trading_mode: str = "live",
) -> dict:
    """账户概览：权益、可用、冻结、领取状态（读取系统设置）"""
    account = await ensure_paper_account(session, user_id, trading_mode)
    settings = await ensure_system_settings(session)
    period_key = current_claim_period_key(
        settings.paper_claim_period,
        settings.paper_claim_reset_hour,
    )
    amount = Decimal(str(settings.paper_claim_amount))
    cap = Decimal(str(settings.paper_total_claim_cap))
    can_claim = account.last_claim_month != period_key and amount > 0
    if cap > 0 and account.total_claimed >= cap:
        can_claim = False
    position_margin = await _sum_position_margin(session, account.id)
    custom_deposited = (await session.execute(
        select(func.coalesce(func.sum(PaperLedger.amount), 0)).where(
            PaperLedger.account_id == account.id,
            PaperLedger.ref_type == "custom_deposit",
        )
    )).scalar_one()

    # 总权益 = 可用 + 冻结（含挂单冻结）
    total_equity = account.balance + account.frozen_margin
    available = account.balance
    risk_rate = Decimal("0")
    if total_equity > 0:
        used = account.frozen_margin
        risk_rate = (used / total_equity * Decimal("100")).quantize(Decimal("0.01"))

    return {
        "account_id": str(account.id),
        "currency": account.currency,
        "balance": float(account.balance),
        "available_margin": float(available),
        "frozen_margin": float(account.frozen_margin),
        "position_margin": float(position_margin),
        "total_equity": float(total_equity),
        "equity_basis": "book_balance_plus_frozen_excludes_floating",
        "legacy_pnl_fields_available": False,
        "realized_pnl": float(account.realized_pnl),
        "unrealized_pnl": 0.0,
        "today_pnl": 0.0,
        "total_claimed": float(account.total_claimed),
        "total_deposit": float(account.total_claimed + custom_deposited),
        "funding_policy": "custom",
        "custom_deposit_enabled": True,
        "simulation_only": True,
        "funding_method_version": "paper-custom-funds-v1",
        "total_withdraw": 0.0,
        "risk_rate": float(risk_rate),
        "last_claim_month": account.last_claim_month,
        "current_claim_month": period_key,
        "current_claim_period": period_key,
        "claim_period": settings.paper_claim_period,
        "claim_reset_hour": int(settings.paper_claim_reset_hour),
        "can_claim": can_claim,
        "claim_amount": float(amount),
        "total_claim_cap": float(cap),
        "updated_at": _iso_utc(account.updated_at),
    }


async def list_ledgers(
    session: AsyncSession,
    user_id: UUID,
    limit: int,
    offset: int,
    trading_mode: str = "live",
) -> dict:
    """分页查询资金流水"""
    account = await ensure_paper_account(session, user_id, trading_mode)
    safe_limit = max(1, min(limit, 100))
    safe_offset = max(0, offset)

    count_stmt = (
        select(func.count())
        .select_from(PaperLedger)
        .where(PaperLedger.account_id == account.id)
    )
    total = int((await session.execute(count_stmt)).scalar_one())

    stmt = (
        select(PaperLedger)
        .where(PaperLedger.account_id == account.id)
        .order_by(PaperLedger.created_at.desc())
        .offset(safe_offset)
        .limit(safe_limit)
    )
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    items = [
        {
            "id": str(row.id),
            "time": _iso_utc(row.created_at) or "",
            "type": row.flow_type,
            "amount": float(row.amount),
            "balance": float(row.balance_after),
            "description": row.description,
            "ref_type": row.ref_type,
            "ref_id": str(row.ref_id) if row.ref_id else None,
        }
        for row in rows
    ]
    return {"total": total, "items": items}
