"""实时 K线 bar → PG 持久化（SimNow tick 聚合后增量写入）

在 update_realtime_klines 的跨周期回调里，每根 1m bar 收盘时调用，
增量写入 PG kline_minute。当天 TqSdk 定时修正（09:10/15:30）会覆盖
这些数据为权威值。

设计要点：
- session_factory 由 scheduler 注入；None 时静默跳过（不影响实时聚合）
- 进程内节流：同一根 bar（symbol + bar_time）只写一次，减少无效 DB 调用
- 只写 1m（多周期由 aggregate_from_1m 现算）
- ON CONFLICT DO NOTHING（SimNow 写入不覆盖已有 TqSdk 权威数据）
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("qihuo.kline_persist")

# 模块级 session_factory（由 scheduler 注入）
_session_factory: Any = None

# 进程内已写标记：{(symbol, bar_time)} —— 避免同一根 bar 重复写 DB
_written: set[tuple[str, str]] = set()
# 防止 _written 无限增长：超过此值时清空
_MAX_WRITTEN_CACHE = 10000


def set_session_factory(sf: Any) -> None:
    """注入 AsyncSession 工厂（scheduler 启动时调用）"""
    global _session_factory
    _session_factory = sf


def _trim_written_cache() -> None:
    """防止 _written 集合无限增长"""
    global _written
    if len(_written) > _MAX_WRITTEN_CACHE:
        _written.clear()


async def persist_closed_bar(
    symbol: str,
    bar: dict[str, Any],
) -> int:
    """把一根已收盘的 1m bar 写入 PG kline_minute

    Args:
        symbol: 合约符号，如 "rb2610"
        bar: bar dict，含 time/open/high/low/close/volume/open_interest

    Returns:
        写入条数（0 = 跳过/已写/失败）
    """
    global _session_factory
    if _session_factory is None:
        return 0

    if not bar or not bar.get("time"):
        return 0

    bar_time = str(bar["time"])
    # 节流：同一根 bar 只写一次
    cache_key = (symbol, bar_time)
    if cache_key in _written:
        return 0
    _written.add(cache_key)
    _trim_written_cache()

    # 只写 1m（多周期不冗余存储）
    try:
        from app.data.contracts import get_code_for_symbol
        from app.services.kline_minute import upsert_minute_bars

        code = get_code_for_symbol(symbol).lower()
        if not code:
            return 0

        async with _session_factory() as session:
            return await upsert_minute_bars(session, code, symbol, "1m", [bar])
    except Exception:
        logger.debug("persist_closed_bar 失败 %s", symbol, exc_info=True)
        return 0
