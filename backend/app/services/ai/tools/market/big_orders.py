"""行情 AI 工具：今日大单（逐秒成交按手数聚合，供 AI 分析大单流向）"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.notify.trade_tick_svc import list_ticks, today_trading_day

logger = logging.getLogger("qihuo.ai.tools_market")

# 返回给模型的 Top 大单条数上限
_TOP_N = 15


async def tool_get_big_orders(
    session: AsyncSession | None,
    symbol: str,
    min_volume: int | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """今日某合约大单统计：多空大单量对比 + Top 大单列表。

    数据源为逐秒成交全量表（trade_tick），min_volume 为大单手数门槛
    （默认 100，活跃品种建议 200-500）；聚合在工具内完成，只回摘要。
    """
    sym = symbol.strip().lower()
    if not sym:
        return {"success": False, "error": "缺少 symbol 参数"}
    if session is None:
        return {"success": False, "error": "数据库会话不可用"}
    threshold = 100 if min_volume is None else max(1, int(min_volume))
    max_rows = 2000 if limit is None else min(max(1, int(limit)), 5000)

    try:
        rows = await list_ticks(
            session,
            symbol=sym,
            min_volume=threshold,
            trading_day=today_trading_day(),
            limit=max_rows,
        )
    except Exception as exc:
        logger.warning("大单工具查询失败 symbol=%s err=%s", sym, exc)
        return {"success": False, "error": f"大单数据查询失败: {exc}"}

    buy_volume = sell_volume = unknown_volume = 0
    buy_count = sell_count = 0
    for row in rows:
        vol = int(row.volume or 0)
        direction = str(row.direction or "")
        if direction == "buy":
            buy_volume += vol
            buy_count += 1
        elif direction == "sell":
            sell_volume += vol
            sell_count += 1
        else:
            unknown_volume += vol

    top_rows = sorted(rows, key=lambda r: int(r.volume or 0), reverse=True)[:_TOP_N]
    top = [
        {
            "time": row.occurred_at.strftime("%H:%M:%S") if row.occurred_at else "",
            "direction": row.direction or "unknown",
            "price": row.price,
            "volume": int(row.volume or 0),
        }
        for row in top_rows
    ]

    if not rows:
        return {
            "success": True,
            "symbol": sym,
            "trading_day": str(today_trading_day()),
            "threshold": threshold,
            "total_count": 0,
            "note": "今日暂无达到门槛的逐笔成交",
        }
    return {
        "success": True,
        "symbol": sym,
        "trading_day": str(today_trading_day()),
        "threshold": threshold,
        "total_count": len(rows),
        "buy_count": buy_count,
        "sell_count": sell_count,
        "buy_volume": buy_volume,
        "sell_volume": sell_volume,
        "unknown_volume": unknown_volume,
        "net_volume": buy_volume - sell_volume,
        "top": top,
    }
