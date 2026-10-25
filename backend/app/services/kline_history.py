"""K 线历史数据 PostgreSQL 查询层 —— 从 kline_history 表读取日线历史"""

from datetime import date
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.kline_history import KlineHistory


async def query_history_bars(
    session: AsyncSession,
    code: str,
    end_date: str,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """查询 PG 中的日线历史数据

    Args:
        session: 数据库会话
        code: 品种代码，如 "AU"
        end_date: 查询此日期之前的记录，格式 "2026-06-03"
        limit: 最多返回条数

    Returns:
        标准化 bar 列表 [{time, open, high, low, close, volume}]，按时间升序
    """
    end = date.fromisoformat(end_date)
    stmt = (
        select(KlineHistory)
        .where(
            KlineHistory.code == code,
            KlineHistory.trade_date < end,
            KlineHistory.market_type == "futures",
        )
        .order_by(KlineHistory.trade_date.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    rows = result.scalars().all()
    # 反转为升序
    return [_row_to_bar(r) for r in reversed(rows)]


async def get_latest_history_date(
    session: AsyncSession,
    code: str,
) -> str | None:
    """查询品种在 PG 中的最新日期"""
    stmt = select(func.max(KlineHistory.trade_date)).where(
        KlineHistory.code == code,
        KlineHistory.market_type == "futures",
    )
    result = await session.execute(stmt)
    latest = result.scalar_one_or_none()
    return latest.isoformat() if latest else None


async def list_mining_symbols(session: AsyncSession) -> list[dict[str, Any]]:
    """列出 PG 中有日线历史数据的品种（供超级因子挖掘下拉）

    返回 [{code, name, symbol, symbol_name, bars, date_from, date_to}]，按 bars 倒序。
    code 是品种代码（如 rb/AU），name 取中文品种名，bars 是数据根数。
    symbol/symbol_name 是主力合约符号与名称（如 rb2701/螺纹钢2701）——
    与因子实验室/AI交易的品种选项同口径，收藏后创建任务能自动选中；
    静态合约表中无映射的品种该字段为 None，前端回退展示品种 code。
    只有这里返回的品种才能做超长挖掘（loader 直查 PG）。
    """
    from app.data.contracts import (
        ALL_CONTRACTS,
        get_contract_name,
        get_master_symbol_for_code,
        get_product_name,
    )

    known_codes = {str(contract["code"]).upper() for contract in ALL_CONTRACTS if contract.get("code")}
    stmt = (
        select(
            KlineHistory.code,
            func.count().label("bars"),
            func.min(KlineHistory.trade_date).label("date_from"),
            func.max(KlineHistory.trade_date).label("date_to"),
        )
        .where(KlineHistory.market_type == "futures", func.upper(KlineHistory.code).in_(known_codes))
        .group_by(KlineHistory.code)
        .order_by(func.count().desc())
    )
    result = await session.execute(stmt)
    out: list[dict[str, Any]] = []
    for code, bars, date_from, date_to in result.all():
        master = get_master_symbol_for_code(code)
        out.append(
            {
                "code": code,
                "name": get_product_name(code),
                "symbol": master if master != code else None,
                "symbol_name": get_contract_name(master) if master != code else None,
                "bars": int(bars),
                "date_from": date_from.isoformat() if date_from else None,
                "date_to": date_to.isoformat() if date_to else None,
            }
        )
    return out


def _row_to_bar(row: KlineHistory) -> dict[str, Any]:
    """ORM 行 → 前端 bar 格式"""
    return {
        "time": row.trade_date.isoformat(),
        "open": row.open,
        "high": row.high,
        "low": row.low,
        "close": row.close,
        "volume": row.volume,
    }


async def upsert_history_bars(
    session: AsyncSession,
    code: str,
    bars: list[dict[str, Any]],
) -> int:
    """批量 upsert 日线到 PG kline_history（ON CONFLICT 更新，返回处理条数）

    用 ON CONFLICT DO UPDATE（不是 DO NOTHING）：TqSdk 修正是以权威数据源
    覆盖可能不准的实时聚合数据，同一日数据必须更新。
    唯一约束：uq_code_date (code, trade_date)。

    Args:
        session: 数据库会话
        code: 品种代码（小写，如 "rb"）
        bars: bar 列表，每项含 time(YYYY-MM-DD)/open/high/low/close/volume/hold
    """
    if not bars:
        return 0
    rows = []
    for b in bars:
        try:
            trade_date = date.fromisoformat(str(b["time"])[:10])
        except ValueError:
            continue
        rows.append(
            {
                "code": code,
                "trade_date": trade_date,
                "open": float(b["open"]),
                "high": float(b["high"]),
                "low": float(b["low"]),
                "close": float(b["close"]),
                "volume": int(b.get("volume", 0) or 0),
                "hold": int(b.get("hold", 0) or 0),
            }
        )
    if not rows:
        return 0
    BATCH_SIZE = 2000
    total = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        stmt = pg_insert(KlineHistory).values(batch)
        # 冲突时更新所有价格/量字段（code + trade_date 是唯一键，不变）
        stmt = stmt.on_conflict_do_update(
            constraint="uq_code_date",
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
                "hold": stmt.excluded.hold,
            },
        )
        result = await session.execute(stmt)
        total += result.rowcount or 0
    await session.commit()
    return total
