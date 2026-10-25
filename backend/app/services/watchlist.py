"""自选行情 CRUD 服务 —— 数据库操作与 Redis 行情合并"""

import json
import logging
from uuid import UUID

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.watchlist import Watchlist
from app.schemas.watchlist import WatchlistCreate

logger = logging.getLogger(__name__)


def resolve_current_master(symbol: str) -> tuple[str, str]:
    """自选 symbol → (当前主力 symbol, 最新名称)；旧月份自动跟随换月

    合约表换月后自选里存的旧月份（rb2610）按品种前缀映射到当前主力
    （rb2701），否则行情列表（只有当前主力）与自选无交集，前端
    「★ 自选」分组会整组消失。未知品种原样返回（名称空串，
    调用方保留库中原名）。
    """
    from app.data.contracts import (
        get_code_for_symbol,
        get_contract_name,
        get_master_symbol_for_code,
    )

    sym = str(symbol or "").strip().lower()
    code = get_code_for_symbol(sym)
    master = get_master_symbol_for_code(code) if code else ""
    if master and master != sym:
        return master, get_contract_name(master)
    return sym, ""



def normalize_market_type(value: str | None) -> str:
    """市场归一化：6位代码视为 A股，其余按传入值（默认期货）"""
    text = str(value or "").strip().lower()
    return "stock" if text == "stock" else "futures"


async def add_to_watchlist(
    session: AsyncSession,
    user_id: UUID,
    data: WatchlistCreate,
    trading_mode: str = "live",
) -> Watchlist:
    """添加合约到自选列表（按交易盘 + 市场双隔离）

    sort_order 自动设为当前用户该盘该市场最大值 +1。
    如果已存在则抛出 ValueError。
    """
    from app.core.trading_mode import normalize_trading_mode

    mode = normalize_trading_mode(trading_mode)
    # A股（6位代码）不走期货主力归一化；期货旧月份跟随换月避免重复
    raw_symbol = str(data.contract_symbol).strip()
    if raw_symbol.isdigit() and len(raw_symbol) == 6:
        market = "stock"
        symbol, fresh_name = raw_symbol, ""
    else:
        market = normalize_market_type(getattr(data, "market_type", None))
        symbol, fresh_name = resolve_current_master(data.contract_symbol)
    name = fresh_name or data.contract_name
    existing = await session.execute(
        select(Watchlist).where(
            and_(
                Watchlist.user_id == user_id,
                Watchlist.trading_mode == mode,
                Watchlist.market_type == market,
                Watchlist.contract_symbol == symbol,
            )
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise ValueError("合约已在自选列表中")

    max_order_result = await session.execute(
        select(func.coalesce(func.max(Watchlist.sort_order), -1)).where(
            Watchlist.user_id == user_id,
            Watchlist.trading_mode == mode,
            Watchlist.market_type == market,
        )
    )
    max_order: int = max_order_result.scalar_one()
    next_order = max_order + 1

    item = Watchlist(
        user_id=user_id,
        trading_mode=mode,
        market_type=market,
        contract_symbol=symbol,
        contract_name=name,
        sort_order=next_order,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


async def remove_from_watchlist(
    session: AsyncSession,
    user_id: UUID,
    item_id: UUID,
    trading_mode: str = "live",
) -> bool:
    """删除自选记录，返回是否成功删除"""
    from app.core.trading_mode import normalize_trading_mode

    mode = normalize_trading_mode(trading_mode)
    result = await session.execute(
        select(Watchlist).where(
            and_(
                Watchlist.id == item_id,
                Watchlist.user_id == user_id,
                Watchlist.trading_mode == mode,
            )
        )
    )
    item = result.scalar_one_or_none()
    if item is None:
        return False
    await session.delete(item)
    await session.commit()
    return True


async def list_watchlist(
    session: AsyncSession,
    user_id: UUID,
    trading_mode: str = "live",
    market_type: str = "futures",
) -> list[Watchlist]:
    """获取用户自选列表（按 sort_order 升序，分盘 + 分市场）"""
    from app.core.trading_mode import normalize_trading_mode

    mode = normalize_trading_mode(trading_mode)
    market = normalize_market_type(market_type)
    result = await session.execute(
        select(Watchlist)
        .where(
            Watchlist.user_id == user_id,
            Watchlist.trading_mode == mode,
            Watchlist.market_type == market,
        )
        .order_by(Watchlist.sort_order)
    )
    return list(result.scalars().all())


async def update_sort_order(
    session: AsyncSession,
    user_id: UUID,
    items: list[dict],
) -> None:
    """批量更新自选排序

    Args:
        items: [{"id": UUID, "sort_order": int}, ...]
    """
    for item_data in items:
        result = await session.execute(
            select(Watchlist).where(
                and_(Watchlist.id == item_data["id"], Watchlist.user_id == user_id)
            )
        )
        item = result.scalar_one_or_none()
        if item is not None:
            item.sort_order = item_data["sort_order"]
    await session.commit()


async def update_group(
    session: AsyncSession,
    user_id: UUID,
    item_id: UUID,
    group_name: str | None,
) -> Watchlist | None:
    """更新自选记录的分组名称"""
    result = await session.execute(
        select(Watchlist).where(
            and_(Watchlist.id == item_id, Watchlist.user_id == user_id)
        )
    )
    item = result.scalar_one_or_none()
    if item is None:
        return None
    item.group_name = group_name
    await session.commit()
    await session.refresh(item)
    return item


async def merge_quotes_to_watchlist(
    watchlist_items: list[Watchlist],
    redis_conn,
) -> list[dict]:
    """将 Redis 缓存的实时行情数据合并到自选列表（按条目 trading_mode）"""
    from app.services.market_keys import get_quote_json

    if not watchlist_items:
        return []

    result: list[dict] = []
    for item in watchlist_items:
        mode = str(getattr(item, "trading_mode", None) or "live")
        # 旧月份自选跟随换月 → 当前主力（读时映射，不改库）
        symbol, fresh_name = resolve_current_master(item.contract_symbol)
        item_dict = {
            "id": str(item.id),
            "contract_symbol": symbol,
            "contract_name": fresh_name or item.contract_name,
            "sort_order": item.sort_order,
            "group_name": item.group_name,
            "created_at": item.created_at.isoformat() if item.created_at else None,
        }
        quote = await get_quote_json(
            redis_conn, mode, symbol
        )
        if quote:
            item_dict["last_price"] = quote.get("last_price")
            item_dict["change"] = quote.get("change")
            item_dict["change_pct"] = quote.get("change_pct")
            item_dict["bid_price"] = quote.get("bid_price")
            item_dict["ask_price"] = quote.get("ask_price")
            item_dict["volume"] = quote.get("volume")
        result.append(item_dict)
    return result
