"""行情数据服务 —— 新浪实时行情 + Redis 缓存/发布

通过 hq.sinajs.cn 直接获取实时期货行情数据，
失败时跳过本次更新（不降级 mock quote，避免污染 K 线）。
"""

import json
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Any

import redis.asyncio as aioredis

from app.data.contracts import ALL_CONTRACTS, get_code_for_symbol, get_decimal_places
from app.services.market_channel import fetch_quotes_for_channel

logger = logging.getLogger("qihuo.market")

# 北京时间（UTC+8）
_BJ_TZ = timezone(timedelta(hours=8))

# Redis 缓存 key 与 TTL
_CONTRACTS_CACHE_KEY = "market:contracts"
_CONTRACTS_CACHE_TTL = 3600  # 1 小时

# Redis Pub/Sub 行情频道
_QUOTES_CHANNEL = "market:quotes"

# ========== 异步接口（供调度器和 API 调用） ==========


def get_static_contracts() -> list[dict[str, str]]:
    """获取静态合约列表（内置数据，无需网络请求）"""
    return [{"symbol": c["symbol"], "name": c["name"], "exchange": c["exchange"]} for c in ALL_CONTRACTS]


async def refresh_contracts_cache(redis_conn: aioredis.Redis) -> list[dict[str, str]]:
    """将合约列表写入 Redis 缓存"""
    contracts = get_static_contracts()
    await redis_conn.set(
        _CONTRACTS_CACHE_KEY,
        json.dumps(contracts, ensure_ascii=False),
        ex=_CONTRACTS_CACHE_TTL,
    )
    logger.info("合约列表缓存已刷新，共 %d 个合约", len(contracts))
    return contracts


async def get_contracts_from_cache(redis_conn: aioredis.Redis) -> list[dict[str, str]]:
    """从 Redis 缓存读取合约列表，缓存不存在时写入"""
    cached = await redis_conn.get(_CONTRACTS_CACHE_KEY)
    if cached is not None:
        return json.loads(cached)
    return await refresh_contracts_cache(redis_conn)


async def fetch_quotes_async(
    redis_conn: aioredis.Redis,
) -> list[dict[str, Any]]:
    """按当前渠道异步拉取行情（源键 Redis / bridge 统一键）"""
    return await fetch_quotes_for_channel(redis_conn)


async def publish_quotes(
    redis_conn: aioredis.Redis,
    quotes: list[dict[str, Any]],
) -> None:
    """将行情数据发布到 Redis Pub/Sub 频道 market:quotes"""
    if not quotes:
        return
    message = json.dumps({"type": "quote", "data": quotes}, ensure_ascii=False)
    await redis_conn.publish(_QUOTES_CHANNEL, message)


async def fetch_and_publish(
    redis_conn: aioredis.Redis,
) -> list[dict[str, Any]]:
    """拉取行情并发布到 Redis Pub/Sub（供定时任务调用）

    渠道不可用时返回空 list，scheduler 跳过本次更新，
    保持 Redis 缓存为上一次真实值，避免 mock 污染 K 线。

    仅推送「当前交易时段内」的品种，避免 7×24 仿真在休市改价。
    """
    from app.services.trading_hours import is_symbol_trading

    quotes = await fetch_quotes_async(redis_conn)
    if not quotes:
        logger.warning("行情拉取为空，跳过本次更新（不降级 mock）")
        return []
    open_quotes: list[dict[str, Any]] = []
    for q in quotes:
        symbol = str(q.get("symbol") or "")
        if not symbol or not is_symbol_trading(symbol):
            continue
        code = get_code_for_symbol(symbol)
        q["decimal_places"] = get_decimal_places(code)
        # 接收时间戳统一盖戳（VVTR/CTP 桥各自已盖；新浪在发布前补）
        # —— WS 出网回退帧过滤按 recv_ts 判序，所有源必须有
        q.setdefault("recv_ts", time.time())
        open_quotes.append(q)
    if open_quotes:
        await publish_quotes(redis_conn, open_quotes)
    return open_quotes


# ========== 盘口与成交 ==========


def generate_orderbook(
    quote: dict[str, Any],
    prev_quote: dict[str, Any] | None = None,
    previous_panel: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """生成服务端统一盘口面板；不伪造缺失的深度档位。"""
    from app.services.market_panel import build_market_panel

    return build_market_panel(quote, prev_quote, previous_panel)


def generate_tick_trade(
    orderbook: dict[str, Any],
    quote: dict[str, Any],
) -> list[dict[str, Any]]:
    """基于盘口单秒增量生成「单条」真实成交（不拆分）。

    替代 generate_trades 的拆分逻辑：1 笔 700 手大单只产生 1 条 volume=700，
    避免拆成 5 笔 140 导致成交列表重复堆积。与 trade_tick / 大单预警同源同口径。
    无增量（current_volume<=0）返回空列表。
    """
    stats = orderbook.get("stats") or {}
    cv_field = stats.get("current_volume")
    current_volume = cv_field.get("value") if isinstance(cv_field, dict) else cv_field
    if not isinstance(current_volume, int) or current_volume <= 0:
        return []
    direction = str(orderbook.get("last_direction") or "").strip().lower()
    if direction not in ("buy", "sell"):
        direction = "buy"
    # 四路开平估算（与 trade_tick 落库同源同算法），随帧分发供前端秒级增量累积；
    # oi_delta 缺失（sina 源/数据异常）时四路为 None，前端只累积总手
    oi_field = stats.get("oi_delta")
    oi_delta = oi_field.get("value") if isinstance(oi_field, dict) else oi_field
    if isinstance(oi_delta, bool) or not isinstance(oi_delta, int):
        oi_delta = None
    if oi_delta is None:
        open_long = close_long = open_short = close_short = None
    else:
        from app.services.notify.trade_tick_svc import split_open_close

        vol_buy = current_volume if direction == "buy" else 0
        vol_sell = current_volume if direction == "sell" else 0
        open_long, close_long, open_short, close_short = split_open_close(
            vol_buy, vol_sell, oi_delta
        )
    last_price = float(quote.get("last_price") or 0)
    if last_price <= 0:
        return []
    # 价格按品种 tick 取整，与 trade_tick 落库同口径（成交量分布前端按 price 聚合对齐）
    frame_symbol = str(quote.get("symbol") or orderbook.get("symbol") or "").strip().lower()
    if frame_symbol:
        from app.services.notify.trade_tick_svc import _round_price

        last_price = _round_price(last_price, frame_symbol)
    tick_time = str(quote.get("tick_time") or "").strip()
    if len(tick_time) == 6 and tick_time.isdigit():
        time_str = f"{tick_time[0:2]}:{tick_time[2:4]}:{tick_time[4:6]}"
    elif ":" in tick_time:
        time_str = tick_time[-8:] if len(tick_time) >= 8 else tick_time
    else:
        time_str = datetime.now(_BJ_TZ).strftime("%H:%M:%S")
    # 完整时间戳（交易日 + 时刻）：跨夜盘/重启的排序与审计用。
    # 交易日优先交易所口径（quote.trade_date，夜盘已归次一交易日），
    # 缺失回退服务器交易日归属。time 保持 HH:MM:SS 展示兼容。
    trade_date = str(quote.get("trade_date") or "").strip()
    if not trade_date:
        try:
            from app.services.trading_hours import beijing_now, get_trading_day

            trade_date = get_trading_day(beijing_now()).isoformat()
        except Exception:
            trade_date = datetime.now(_BJ_TZ).strftime("%Y-%m-%d")
    return [
        {
            "time": time_str,
            "ts": f"{trade_date} {time_str}",
            "price": last_price,
            "volume": current_volume,
            "direction": direction,
            "source": "volume_delta",
            "open_long": open_long,
            "close_long": close_long,
            "open_short": open_short,
            "close_short": close_short,
        }
    ]


def generate_trades(
    quote: dict[str, Any],
    prev_quote: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """由累计成交量增量推导成交明细（非随机伪造）

    新浪源无真实逐笔 tick：用 volume 差值作为本轮成交手数，
    价格取最新价，方向由最新价相对前次价推断。
    无增量时返回空列表。
    """
    if prev_quote is None:
        return []

    try:
        cur_vol = int(float(quote.get("volume") or 0))
        prev_vol = int(float(prev_quote.get("volume") or 0))
    except (TypeError, ValueError):
        return []

    delta = cur_vol - prev_vol
    if delta <= 0:
        return []

    last_price = float(quote.get("last_price") or 0)
    if last_price <= 0:
        return []

    prev_last = float(prev_quote.get("last_price") or last_price)
    if last_price > prev_last:
        direction = "buy"
    elif last_price < prev_last:
        direction = "sell"
    else:
        # 价格不动：用买一卖一相对位置粗判
        bid = float(quote.get("bid_price") or 0)
        ask = float(quote.get("ask_price") or 0)
        if bid and last_price <= bid:
            direction = "sell"
        elif ask and last_price >= ask:
            direction = "buy"
        else:
            direction = "buy"

    # 时间：优先 tick_time，否则北京时间 HH:MM:SS
    tick_time = str(quote.get("tick_time") or "").strip()
    if len(tick_time) == 6 and tick_time.isdigit():
        # 新浪商品字段有时是 HHmmss
        time_str = f"{tick_time[0:2]}:{tick_time[2:4]}:{tick_time[4:6]}"
    elif ":" in tick_time:
        time_str = tick_time[-8:] if len(tick_time) >= 8 else tick_time
    else:
        time_str = datetime.now(_BJ_TZ).strftime("%H:%M:%S")

    # 单轮最多拆 5 笔，避免列表爆炸；总量仍等于 delta
    max_splits = 5
    if delta <= max_splits:
        lots = [1] * delta
    else:
        base = delta // max_splits
        rem = delta % max_splits
        lots = [base + (1 if i < rem else 0) for i in range(max_splits)]

    return [
        {
            "time": time_str,
            "price": last_price,
            "volume": lot,
            "direction": direction,
            "source": "volume_delta",
        }
        for lot in lots
        if lot > 0
    ]


async def cache_orderbook(
    redis_conn: aioredis.Redis,
    orderbook: dict[str, Any],
    trading_mode: str = "live",
) -> None:
    """将盘口写入 Redis（live 双写旧键）"""
    from app.services.market_keys import write_keys_for_orderbook

    symbol: str = orderbook["symbol"]
    payload = json.dumps(orderbook, ensure_ascii=False)
    for key in write_keys_for_orderbook(trading_mode, symbol):
        await redis_conn.set(key, payload, ex=120)


async def cache_trades(
    redis_conn: aioredis.Redis,
    symbol: str,
    trades: list[dict[str, Any]],
    trading_mode: str = "live",
) -> None:
    """将成交追加到 Redis List（live 双写旧键），保留最近 30 条"""
    from app.services.market_keys import write_keys_for_trades

    if not trades:
        return
    for key in write_keys_for_trades(trading_mode, symbol):
        pipe = redis_conn.pipeline()
        for trade in trades:
            pipe.rpush(key, json.dumps(trade, ensure_ascii=False))
        pipe.ltrim(key, -30, -1)
        pipe.expire(key, 120)
        await pipe.execute()
