"""任务品种持仓浮盈 —— 仅本合约未实现盈亏，不含手续费"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.paper.position import PaperPosition
from app.services.paper.price_band import resolve_last_price


def source_for_task(task: Any) -> str:
    """任务 → 持仓 source：ai 任务=ai；量化类=quant

    PaperPosition 按 (account,symbol,direction,source) 唯一，
    AI 与量化任务的同品种持仓是不同行，必须按 source 隔离。
    """
    st = str(getattr(task, "strategy_type", "") or "").lower()
    return "ai" if st == "ai" else "quant"


async def get_task_position_pnl(
    session: AsyncSession,
    user_id: UUID,
    symbol: str,
    redis_conn: Any | None,
    source: str | None = None,
    task_id: UUID | None = None,
) -> dict[str, Any]:
    """查询任务品种持仓与实时浮盈（按 task_id 隔离，不含手续费）

    优先按 task_id 精确隔离；未传 task_id 时回退到 source 内存过滤，
    兼容 profit_svc / showcase_svc 等历史调用方。

    返回字段供任务列表 / 权益曲线使用：
    - direction: long/short/None
    - quantity, avg_price, last_price
    - unrealized_pnl: 浮动盈亏（元）
    - cash_delta: 同 unrealized_pnl（任务收益展示用，不混账户手续费）
    """
    sym = (symbol or "").strip().lower()
    empty: dict[str, Any] = {
        "direction": None,
        "quantity": 0,
        "avg_price": None,
        "last_price": None,
        "unrealized_pnl": 0.0,
        "cash_delta": 0.0,
        "multiplier": 1,
    }
    if not sym:
        return empty

    filters = [
        PaperPosition.user_id == user_id,
        PaperPosition.symbol == sym,
        PaperPosition.quantity > 0,
    ]
    if task_id is not None:
        # 任务级隔离：按 task_id 精确匹配，同品种不同任务互不串扰
        filters.append(PaperPosition.task_id == task_id)
    elif source is not None:
        # 回退：按 source 过滤（ai/quant）
        filters.append(PaperPosition.source == source)
    stmt = select(PaperPosition).where(*filters)
    pos = (await session.execute(stmt)).scalar_one_or_none()
    if pos is None or int(pos.quantity or 0) <= 0:
        return empty

    qty = int(pos.quantity or 0)
    avg = float(pos.avg_price or 0)
    mult = int(pos.multiplier or 1)
    direction = str(pos.direction or "")
    # 浮盈展示禁用 mock 兜底：无报价时回落持仓均价（浮盈 0），
    # 不能用 mock 基准价虚增浮盈（2026-09-08 rb2701 事故）
    last_ref = await resolve_last_price(sym, redis_conn, None, allow_mock=False)
    last = float(last_ref) if last_ref is not None else avg
    if last <= 0:
        last = avg

    if direction == "long":
        pnl = (last - avg) * qty * mult
    elif direction == "short":
        pnl = (avg - last) * qty * mult
    else:
        pnl = 0.0

    # 量化到分
    pnl = float(Decimal(str(pnl)).quantize(Decimal("0.01")))
    return {
        "direction": direction,
        "quantity": qty,
        "avg_price": avg,
        "last_price": last,
        "unrealized_pnl": pnl,
        "cash_delta": pnl,
        "multiplier": mult,
    }
