"""大单检测 —— 显著放量即推送原始 big_order_tick 事件

不读取用户阈值（那是前端职责）。scheduler 在生成 orderbook 后调用本模块：
对每个 symbol 比较本次累计成交量与上次记录，若单笔增量（current_volume）≥ 市场基线，
则发布 big_order_tick 事件到 Redis pub/sub，由 WS 投递给对应 trading_mode 的在线客户端。

进程内维护 _last_volume 缓存（按 trading_mode + symbol），仅持锁的 master worker 运行，
避免多 worker 重复推送。
"""

from __future__ import annotations

import logging
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger("qihuo.big_order_detect")

# 大单检测市场基线（手）：超过即视为显著放量并推送原始事件。
# 前端再按用户阈值二次判定。这是"全合约触发"的粗筛门槛。
_BIG_ORDER_BASELINE = 50

# Redis pub/sub 频道：按 trading_mode 分频道
_BIG_ORDER_CHANNEL_TEMPLATE = "ws:big-order:{mode}"


def _channel(mode: str) -> str:
    return _BIG_ORDER_CHANNEL_TEMPLATE.format(mode=mode)


# 进程内：上次累计成交量缓存（mode,symbol → total_volume）
_last_total: dict[tuple[str, str], int] = {}


def reset_detect_state() -> None:
    """重置进程内缓存（测试用）"""
    _last_total.clear()


def _stat_value(orderbook: dict[str, Any], key: str) -> Any:
    """读取 orderbook.stats[key].value，兼容 dict 与裸值"""
    stats = orderbook.get("stats")
    if not isinstance(stats, dict):
        return None
    field = stats.get(key)
    if isinstance(field, dict):
        return field.get("value")
    return field


async def detect_and_publish(
    redis_conn: aioredis.Redis,
    trading_mode: str,
    orderbook: dict[str, Any],
) -> int:
    """检测单个 orderbook 是否显著放量，命中则发布事件。

    返回本次发布的事件数（0 或 1）。orderbook 来自 build_market_panel，
    含 stats.current_volume 与顶层 last_direction / symbol。
    """
    symbol = str(orderbook.get("symbol") or "").strip().lower()
    if not symbol:
        return 0

    # 先建立/更新累计总量缓存（无论是否达基线，便于后续去重对比）
    cache_key = (trading_mode, symbol)
    quote_total = _stat_value(orderbook, "volume")
    cur_total = int(quote_total) if isinstance(quote_total, (int, float)) else None
    prev_total = _last_total.get(cache_key)

    current_volume = _stat_value(orderbook, "current_volume")
    direction = str(orderbook.get("last_direction") or "").strip().lower()

    # 首次见到该 symbol：仅记录总量，不推送（避免冷启动误报）
    if prev_total is None:
        if cur_total is not None:
            _last_total[cache_key] = cur_total
        return 0

    # 更新总量缓存（后续逻辑都基于本次总量）
    if cur_total is not None:
        _last_total[cache_key] = cur_total

    # 预过滤：无增量 / 无方向 / 低于基线 / 总量未增长 → 不推送
    if (
        not isinstance(current_volume, int)
        or current_volume <= 0
        or direction not in ("buy", "sell")
        or current_volume < _BIG_ORDER_BASELINE
        or cur_total is None
        or cur_total <= prev_total
    ):
        return 0

    price = _stat_value(orderbook, "average_price")
    if not isinstance(price, (int, float)) or price <= 0:
        # 兜底用 last_price（部分 quote 直接带）
        price = None

    payload = {
        "type": "big_order_tick",
        "data": {
            "symbol": symbol,
            "direction": direction,
            "volume": current_volume,
            "price": price,
            "time": orderbook.get("asof"),
        },
    }
    import json

    try:
        await redis_conn.publish(
            _channel(trading_mode),
            json.dumps(payload, ensure_ascii=False),
        )
        return 1
    except Exception:
        logger.exception("big_order_tick 发布失败 symbol=%s", symbol)
        return 0


async def detect_batch(
    redis_conn: aioredis.Redis,
    trading_mode: str,
    orderbooks: list[dict[str, Any]],
) -> int:
    """批量检测多个 orderbook，返回总发布事件数"""
    n = 0
    for ob in orderbooks:
        try:
            n += await detect_and_publish(redis_conn, trading_mode, ob)
        except Exception:
            logger.exception("大单检测异常")
    return n


def get_baseline() -> int:
    """返回当前市场基线（手）"""
    return _BIG_ORDER_BASELINE
