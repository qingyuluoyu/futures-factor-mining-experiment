"""全市场逐秒成交增量服务 —— 批量写入 + 查询 + 清空

scheduler 每秒收集有成交的品种 (current_volume>0)，批量写入 trade_tick。
大单历史查询按 symbol/direction/min_volume/trading_day 过滤本表。
每晚 20:30 清空上一交易日。

价格用最新价(last_price)按品种最小变动价位取整，避免均价的小数；
时间用北京时间(beijing_now)。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.contracts import get_tick_size, round_by_tick
from app.data.product_specs import extract_product_code
from app.models.big_order import TradeTick
from app.services.trading_hours import beijing_now, get_trading_day

logger = logging.getLogger("qihuo.trade_tick_svc")

# 北京时区（occurred_at 显式带时区写入，避免 PG 按 UTC 解释导致偏移）
_BJ_TZ = timezone(__import__("datetime").timedelta(hours=8))


def _stat_value(orderbook: dict[str, Any], key: str) -> Any:
    """读取 orderbook.stats[key].value，兼容 dict 与裸值"""
    stats = orderbook.get("stats")
    if not isinstance(stats, dict):
        return None
    field = stats.get(key)
    if isinstance(field, dict):
        return field.get("value")
    return field


def _decimal_places_from_tick(tick: float) -> int:
    """由 tick size 推算小数位：tick>=1 → 0；tick<1 → tick 的小数位"""
    if tick >= 1:
        return 0
    s = f"{tick:.10f}".rstrip("0").split(".")
    return len(s[1]) if len(s) > 1 else 0


def _round_price(price: float, symbol: str) -> float:
    """按品种最小变动价位取整价格，避免小数（如螺纹钢→整数，黄金→2 位）"""
    try:
        code = extract_product_code(symbol)
        tick = get_tick_size(code)
        rounded = round_by_tick(price, code)
        return round(rounded, _decimal_places_from_tick(tick))
    except Exception:
        return float(price)


def split_open_close(
    vol_buy: int,
    vol_sell: int,
    oi_delta: int | None,
) -> tuple[int | None, int | None, int | None, int | None]:
    """将单秒主动买/卖量按持仓变化估算拆分为 开多/平多/开空/平空。

    约束（两方程四未知数，欠定，按整体开仓率比例分摊近似）：
    - 开多 + 平空 = 主动买量；开空 + 平多 = 主动卖量
    - 总开仓 - 总平仓 = ΔOI → 开仓率 r = (ΔOI + 总成交) / (2×总成交)

    买卖两侧按同一开仓率 r 分配各自的开/平（对称中性假设）。
    oi_delta 缺失或无成交时无法拆分，返回全 None。
    """
    if oi_delta is None:
        return None, None, None, None
    total = vol_buy + vol_sell
    if total <= 0:
        return None, None, None, None
    ratio = min(max((oi_delta + total) / (2 * total), 0.0), 1.0)
    open_long = int(min(max(round(vol_buy * ratio), 0), vol_buy))
    close_short = vol_buy - open_long
    open_short = int(min(max(round(vol_sell * ratio), 0), vol_sell))
    close_long = vol_sell - open_short
    return open_long, close_long, open_short, close_short


def collect_tick(
    orderbook: dict[str, Any],
    quote: dict[str, Any],
) -> dict[str, Any] | None:
    """从 orderbook + quote 提取一行 tick；该秒无成交（current_volume<=0）返回 None。

    价格取 quote.last_price（最新价），按品种 tick 取整；
    成交量取 orderbook.stats.current_volume（单秒真实增量，未拆分）；
    四路开平 = direction 明确且 stats.oi_delta 有效时按 split_open_close 估算，否则 None。
    """
    symbol = str(orderbook.get("symbol") or quote.get("symbol") or "").strip().lower()
    if not symbol:
        return None
    current_volume = _stat_value(orderbook, "current_volume")
    if not isinstance(current_volume, int) or current_volume <= 0:
        return None
    direction = str(orderbook.get("last_direction") or "").strip().lower()
    if direction not in ("buy", "sell"):
        direction = ""
    # 价格优先用最新价，按品种 tick 取整（无小数）
    last_price = quote.get("last_price") if isinstance(quote, dict) else None
    try:
        price = float(last_price) if last_price else 0.0
    except (TypeError, ValueError):
        price = 0.0
    if price > 0:
        price = _round_price(price, symbol)
    oi_delta = _stat_value(orderbook, "oi_delta")
    if isinstance(oi_delta, bool) or not isinstance(oi_delta, int):
        oi_delta = None
    if direction and oi_delta is not None:
        vol_buy = current_volume if direction == "buy" else 0
        vol_sell = current_volume if direction == "sell" else 0
        open_long, close_long, open_short, close_short = split_open_close(
            vol_buy, vol_sell, oi_delta
        )
    else:
        open_long = close_long = open_short = close_short = None
    return {
        "symbol": symbol,
        "direction": direction,
        "volume": current_volume,
        "price": price,
        "open_long": open_long,
        "close_long": close_long,
        "open_short": open_short,
        "close_short": close_short,
    }


async def bulk_insert_ticks(
    session: AsyncSession,
    pairs: list[tuple[dict[str, Any], dict[str, Any]]],
) -> int:
    """从一批 (orderbook, quote) 提取有成交的 tick，批量写入。返回写入行数。

    时间用北京时间（beijing_now，显式带 +08:00 时区），交易日由 get_trading_day 归属。
    """
    now_bj = beijing_now()
    trading_day = get_trading_day(now_bj).date()
    rows: list[TradeTick] = []
    for orderbook, quote in pairs:
        tick = collect_tick(orderbook, quote)
        if tick is None:
            continue
        rows.append(
            TradeTick(
                symbol=tick["symbol"],
                direction=tick["direction"],
                volume=tick["volume"],
                price=tick["price"],
                occurred_at=now_bj,
                trading_day=trading_day,
                open_long=tick["open_long"],
                close_long=tick["close_long"],
                open_short=tick["open_short"],
                close_short=tick["close_short"],
            )
        )
    if not rows:
        return 0
    session.add_all(rows)
    await session.commit()
    return len(rows)


async def list_ticks(
    session: AsyncSession,
    symbol: str | None = None,
    direction: str | None = None,
    min_volume: int | None = None,
    trading_day: date | None = None,
    limit: int = 500,
) -> list[TradeTick]:
    """查询逐秒成交（倒序）。direction 为空串表示查方向不明的行。"""
    stmt = select(TradeTick)
    if symbol:
        stmt = stmt.where(TradeTick.symbol == symbol.lower())
    if direction:
        stmt = stmt.where(TradeTick.direction == direction)
    if min_volume is not None:
        stmt = stmt.where(TradeTick.volume >= min_volume)
    if trading_day is not None:
        stmt = stmt.where(TradeTick.trading_day == trading_day)
    stmt = stmt.order_by(TradeTick.occurred_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def volume_profile_ticks(
    session: AsyncSession,
    symbol: str,
    trading_day: date | None = None,
) -> dict[str, Any]:
    """按价格聚合当日逐秒成交，返回成交量分布（含四路开平估算）。

    - price 升序全量返回（价格档几十~几百），阈值筛选由前端实时交互；
    - NULL 四路按 0 聚合，unknown = 总手 - 多单 - 空单（方向不明部分）。
    """
    day = trading_day if trading_day is not None else today_trading_day()
    stmt = (
        select(
            TradeTick.price,
            func.coalesce(func.sum(TradeTick.open_long), 0).label("open_long"),
            func.coalesce(func.sum(TradeTick.close_long), 0).label("close_long"),
            func.coalesce(func.sum(TradeTick.open_short), 0).label("open_short"),
            func.coalesce(func.sum(TradeTick.close_short), 0).label("close_short"),
            func.sum(TradeTick.volume).label("total"),
            func.max(TradeTick.occurred_at).label("asof"),
        )
        .where(TradeTick.symbol == symbol.lower())
        .where(TradeTick.trading_day == day)
        .group_by(TradeTick.price)
        .order_by(TradeTick.price)
    )
    result = await session.execute(stmt)
    rows: list[dict[str, Any]] = []
    asof: datetime | None = None
    for row in result.all():
        open_long = int(row.open_long)
        close_long = int(row.close_long)
        open_short = int(row.open_short)
        close_short = int(row.close_short)
        total = int(row.total)
        buy_total = open_long + close_short
        sell_total = open_short + close_long
        if row.asof is not None and (asof is None or row.asof > asof):
            asof = row.asof
        rows.append(
            {
                "price": float(row.price),
                "open_long": open_long,
                "close_long": close_long,
                "open_short": open_short,
                "close_short": close_short,
                "buy_total": buy_total,
                "sell_total": sell_total,
                "total": total,
            }
        )
    # 最新一笔成交价（前端"最新价置顶"排序锚点；同秒同品种仅一行，occurred_at 倒序取首行）
    last_price: float | None = None
    if rows:
        last_res = await session.execute(
            select(TradeTick.price)
            .where(TradeTick.symbol == symbol.lower())
            .where(TradeTick.trading_day == day)
            .order_by(TradeTick.occurred_at.desc())
            .limit(1)
        )
        last_row = last_res.first()
        if last_row is not None:
            last_price = float(last_row.price)
    return {
        "symbol": symbol.lower(),
        "trading_day": day,
        "asof": asof,
        "last_price": last_price,
        "rows": rows,
    }


async def purge_before_day(session: AsyncSession, cutoff: date) -> int:
    """删除 trading_day 严格小于 cutoff 的全部记录，返回删除行数"""
    result = await session.execute(
        delete(TradeTick).where(TradeTick.trading_day < cutoff)
    )
    await session.commit()
    return int(result.rowcount or 0)


def today_trading_day() -> date:
    """当前归属交易日（供 API 默认值与诊断）"""
    return get_trading_day(beijing_now()).date()
