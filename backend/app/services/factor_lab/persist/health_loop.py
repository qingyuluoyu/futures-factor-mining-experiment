"""因子衰减监控后台循环 —— 每交易日刷新一次收藏因子健康度（IC 口径）

与 factor_mining_loop 同范式：master worker 持有，非交易时段休眠。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from app.services.trading_hours import is_trading_hours

logger = logging.getLogger("qihuo.factor_health")

# 每天刷一次；启动后先等一小段避免与冷启动抢资源
_INTERVAL_SEC = 24 * 3600
_INITIAL_DELAY_SEC = 300
_BARS_LIMIT = 400  # rolling_health 需 250 根 IC 窗 + 余量


async def factor_health_loop(session_factory, redis_conn) -> None:
    """收藏因子健康度日更循环"""
    logger.info("因子衰减监控循环已启动（每日刷新一次收藏因子 health，IC 口径）")
    await asyncio.sleep(_INITIAL_DELAY_SEC)
    while True:
        if not is_trading_hours():
            await asyncio.sleep(3600)
            continue
        try:
            async with session_factory() as session:
                from app.services.factor_lab.persist.health_svc import (
                    refresh_favorites_health,
                )

                stats = await refresh_favorites_health(
                    session,
                    lambda s, tf, lim: _load_bars(s, tf, lim, session_factory),
                )
                logger.info("因子健康刷新完成 %s", stats)
        except Exception:
            logger.exception("因子健康刷新失败（下一周期重试）")
        await asyncio.sleep(_INTERVAL_SEC)


async def _load_bars(symbol: str, timeframe: str, limit: int, session_factory):
    """近端 bars：日线走主库历史，分钟走挖掘 loader（近期口径足够）"""
    if timeframe == "1d":
        from app.data.contracts import get_code_for_symbol
        from app.services.kline_history import query_history_bars

        code = get_code_for_symbol(symbol) or symbol.upper()
        end = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        async with session_factory() as session:
            return await query_history_bars(session, code, end, limit)
    from app.services.factor_mining.loader import load_mining_bars

    async with session_factory() as session:
        bars, _, _ = await load_mining_bars(None, session, symbol, timeframe)
        return bars
