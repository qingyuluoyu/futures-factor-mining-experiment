"""定时任务 —— 行情数据定期拉取与发布

使用 asyncio 原生后台任务替代 APScheduler，
避免与 uvicorn 事件循环的兼容性问题。
非交易时段自动暂停拉取，节省资源。

**多 worker 注意**:
uvicorn --workers N 时每个 worker 都会跑 lifespan/start_scheduler,
但只有持有 Redis 锁的那个 worker 才真正启动后台任务,
避免 N 个 worker 重复拉行情 + 各自维护 _realtime_state 导致数据竞态。

**主力合约识别**:
scheduler 同时启动主力识别任务(每 5 秒一次),扫描全部月份合约的 volume,
按品种代码分组,取 Top1=master / Top2=secondary,结果写入 Redis。
"""

import asyncio
import json
import logging
import os
import socket
import time
from typing import Any

import redis.asyncio as aioredis

from app.data.contracts import ALL_CONTRACTS
from app.services.contract_master import cache_master_contracts
from app.services.kline import update_realtime_klines
from app.services.market import (
    cache_orderbook,
    cache_trades,
    fetch_and_publish,
    generate_orderbook,
    generate_tick_trade,
    refresh_contracts_cache,
)
from app.services.news import collect_and_cache_news
from app.services.trading_hours import is_trading_hours

logger = logging.getLogger("qihuo.scheduler")

# 交易时段拉取间隔（秒）
_FETCH_INTERVAL_TRADING = 1

# 非交易时段拉取间隔（秒），慢速刷新保持缓存存活
_FETCH_INTERVAL_IDLE = 60

# 主力合约识别间隔(秒):每 5 秒一次,够稳定且不过频
_MASTER_INTERVAL = 5

# forming K 线行情源键新鲜度窗口(秒):桥停推后源键靠 TTL 长期残留,
# 超窗的陈旧 quote 不得再喂 forming(sina 桥 vvtr 供数时 60s 轮询,取 90s)
_SOURCE_STALE_SECONDS = 90

# （全月份合约行情拉取已移至 qihuo-sina-bridge 容器，见 app/connectors/sina/bridge.py）

# scheduler 选主锁:持有锁的 worker 才跑后台任务,避免多 worker 重复写 Redis
_SCHEDULER_LOCK_KEY = "scheduler:master-lock"
_SCHEDULER_LOCK_TTL = 20  # 20 秒过期
_SCHEDULER_LOCK_RENEW_INTERVAL = 5  # 每 5 秒续期一次

# 原子续期：仅当 value == worker_id 时 EXPIRE，避免误续别人的锁
_RENEW_LOCK_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('expire', KEYS[1], tonumber(ARGV[2]))
end
return 0
"""

# 原子释放：仅持有者可 DEL
_RELEASE_LOCK_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""

# 后台任务引用
_bg_task: asyncio.Task | None = None
_news_task: asyncio.Task | None = None
_lock_task: asyncio.Task | None = None
_master_task: asyncio.Task | None = None
_ai_trading_task: asyncio.Task | None = None
_factor_mining_task: asyncio.Task | None = None
_factor_health_task: asyncio.Task | None = None
_virtual_side_task: asyncio.Task | None = None
_price_alert_task: asyncio.Task | None = None
_big_order_purge_task: asyncio.Task | None = None
# 记录最近一次清空的北京日期，避免同一天重复触发
_big_order_last_purge_day: str | None = None
_channel_health_task: asyncio.Task | None = None
_channel_preflight_task: asyncio.Task | None = None
_kline_source_health_task: asyncio.Task | None = None
_kline_sync_task: asyncio.Task | None = None
_post_deploy_task: asyncio.Task | None = None
_watchdog_task: asyncio.Task | None = None
_vvtr_bridge_task: asyncio.Task | None = None
_vvtr_tickdata_task: asyncio.Task | None = None
_contracts_sync_task: asyncio.Task | None = None
_kline_reconcile_task: asyncio.Task | None = None
_tickdata_backfill_task: asyncio.Task | None = None
_ai_anchor_task: asyncio.Task | None = None
_kline_prewarm_task: asyncio.Task | None = None
_is_master: bool = False

# 虚拟盘盘口/K 线独立节奏（不跟 live 休市 60s 降频）
_VIRTUAL_SIDE_INTERVAL = 1

# 价格预警检测间隔（秒）：扫 enabled 预警对比最新价
_PRICE_ALERT_INTERVAL = 5

# 大单记录 20:30 清空检查间隔（秒）：每分钟检查一次北京时间是否到 20:30
_BIG_ORDER_PURGE_INTERVAL = 60
_BIG_ORDER_PURGE_HOUR = 20  # 北京时间 20:30 清空上一交易日数据

# K 线盘中对账间隔（秒）：交易时段内每 10 分钟用 VVTR 当根权威值校一次
# forming 桶。每轮每周期仅 1 次 HTTP（多合约批量），不打满 1 rps 预算。
_KLINE_RECONCILE_INTERVAL = 600

# 上次行情快照（按 symbol 合并，避免部分拉取失败清空历史）
_prev_quotes: dict[str, dict[str, Any]] = {}
_prev_panels: dict[str, dict[str, Any]] = {}

# DB 会话工厂（主进程注入，供挂单撮合使用）
_session_factory: Any | None = None


def set_session_factory(session_factory: Any) -> None:
    """注入数据库会话工厂（应用启动时调用）"""
    global _session_factory
    _session_factory = session_factory
    # 同步注入给 kline_persist（实时 1m bar 增量写 PG）
    try:
        from app.services.kline_persist import set_session_factory as _set_kpsf

        _set_kpsf(session_factory)
    except Exception:
        logger.debug("kline_persist session_factory 注入失败", exc_info=True)


async def _bootstrap_live_namespace_from_legacy(
    redis_conn: aioredis.Redis,
) -> None:
    """迁移期引导：旧 market:quote:* 补写到 market:live:quote:*

    休市时 scheduler 不刷新 quote，若不引导则 live 前缀长期为空，
    依赖读路径回落旧键；引导后 live 与 virtual 命名空间对称可读。
    """
    n = 0
    async for key in redis_conn.scan_iter("market:quote:*"):
        kk = key if isinstance(key, str) else key.decode()
        parts = kk.split(":")
        # 仅旧键 market:quote:{symbol}，跳过 market:live/virtual/monthly
        if len(parts) != 3:
            continue
        sym = parts[-1]
        live_key = f"market:live:quote:{sym}"
        try:
            if await redis_conn.exists(live_key):
                continue
            raw = await redis_conn.get(kk)
            if not raw:
                continue
            await redis_conn.set(live_key, raw, ex=7 * 24 * 3600)
            n += 1
        except Exception:
            continue
    logger.info("live 命名空间引导完成，补写 %d 条 quote", n)


async def _process_quotes(
    redis_conn: aioredis.Redis,
    quotes: list[dict[str, Any]],
) -> None:
    """处理行情数据：缓存 quote + 生成并缓存 orderbook/trades

    仅处理当前处于交易时段的品种；休市品种保留 Redis 旧价不刷新。
    """
    global _prev_quotes, _prev_panels

    from app.services.trading_hours import is_symbol_trading

    # 收集本轮 (orderbook, quote) 对，循环结束后批量写入 trade_tick（全市场逐秒成交）
    tick_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []

    for quote in quotes:
        symbol: str = quote["symbol"]
        if not is_symbol_trading(symbol):
            continue

        # 上游已完成 SimNow 主源 + 新浪逐字段兜底，这里只缓存统一快照。
        from app.services.market_keys import (
            write_keys_for_quote,
        )

        # live 双写：market:live:quote:* + 旧 market:quote:*
        payload = json.dumps(quote, ensure_ascii=False)
        for key in write_keys_for_quote("live", symbol):
            await redis_conn.set(key, payload, ex=7 * 24 * 3600)

        prev_quote = _prev_quotes.get(symbol)
        previous_panel = _prev_panels.get(symbol)
        if previous_panel is None:
            try:
                from app.services.market_keys import orderbook_key

                raw_panel = await redis_conn.get(orderbook_key("live", symbol))
                if raw_panel:
                    previous_panel = json.loads(raw_panel)
            except Exception:
                previous_panel = None

        # 盘口与统计统一由服务端计算，行情页和交易页共用同一份结果。
        orderbook = generate_orderbook(quote, prev_quote, previous_panel)
        await cache_orderbook(redis_conn, orderbook, "live")
        _prev_panels[symbol] = orderbook

        # 大单检测：显著放量即推送原始 big_order_tick 事件（前端按用户阈值二次判定）
        try:
            from app.services.notify.big_order_detect import detect_and_publish

            await detect_and_publish(redis_conn, "live", orderbook)
        except Exception:
            logger.exception("live 大单检测异常 %s", symbol)

        # 收集本轮 (orderbook, quote) 用于全量逐秒成交落库
        tick_pairs.append((orderbook, quote))

        # 成交：基于盘口单秒增量生成单条真实成交（不拆分），避免重复堆积
        trades = generate_tick_trade(orderbook, quote)
        await cache_trades(redis_conn, symbol, trades, "live")

        # 按 symbol 合并快照，避免本批缺席合约被当成「首次」
        _prev_quotes[symbol] = quote

    # 全市场逐秒成交批量落库（仅 master worker；有成交才写）
    if tick_pairs and _session_factory is not None:
        try:
            from app.services.notify.trade_tick_svc import bulk_insert_ticks

            async with _session_factory() as session:
                n = await bulk_insert_ticks(session, tick_pairs)
                if n:
                    logger.debug("trade_tick 写入 %d 行", n)
        except Exception:
            logger.exception("trade_tick 批量写入失败")

    # live K 线 forming：real_ctp 渠道用 CTP 交易所行情（OHLC 真实可靠），
    # 其余渠道保持新浪 quote（避免 CTP 仿真 OHLC 污染）
    try:
        quotes = await _live_kline_quotes(redis_conn)
        if quotes:
            await update_realtime_klines(redis_conn, quotes)
    except Exception:
        logger.exception("K 线 forming 更新失败")

    # virtual 缺价补写改由 _background_virtual_side_loop 负责（避免跟 live 休市 60s）

    # 挂单撮合：live 仅开盘用本批行情；virtual 7×24 靠 Redis 全量 pending 扫描
    if _session_factory is None:
        logger.warning("挂单撮合跳过：session_factory 未注入")
    else:
        try:
            from app.services.paper.matching.matcher import (
                match_pending_from_redis,
                match_quotes,
            )

            filled = 0
            if is_trading_hours() and quotes:
                filled += await match_quotes(_session_factory, quotes)
            # 始终扫 pending：virtual 不依赖国内交易时段
            filled += await match_pending_from_redis(
                _session_factory, redis_conn
            )
            if filled:
                logger.info("挂单撮合成交 %d 笔", filled)
        except Exception:
            logger.exception("挂单撮合失败")


async def _live_kline_quotes(
    redis_conn: aioredis.Redis,
) -> list[dict[str, Any]]:
    """live K 线 forming 的行情来源

    real_ctp / vvtr 渠道：从 bridge 写的源键收集（交易所真实 OHLC，替代新浪）；
    其余渠道：sina-bridge 写的 sina 源键（simnow 仿真 OHLC 不可靠，保持原行为）。

    源键带 recv_ts 新鲜度过滤：桥停推后源键靠 TTL(7d) 长期残留，陈旧 quote
    会以昨夜价格持续喂当前桶 forming（2026-08-26 实测 wh/if/ic/im/pm 五个
    非主力陈旧 10.5h 仍在写入）。窗口取 90s——sina 桥自适应降频（vvtr 供数
    时 60s 轮询）下源键最大合法间隔约 60s；recv_ts 缺失放行（兼容老格式，
    最坏退回无过滤行为）。
    """
    from app.core.trading_mode import MODE_LIVE
    from app.services.market_channel import (
        CHANNEL_CTP_SIM,
        CHANNEL_REAL_CTP,
        CHANNEL_VVTR,
        read_channel_from_redis,
    )

    try:
        channel = await read_channel_from_redis(redis_conn, MODE_LIVE)
    except Exception:
        channel = "sina"

    source_label = "sina"
    if channel == CHANNEL_REAL_CTP:
        source_label = "real_ctp"
    elif channel == CHANNEL_CTP_SIM:
        # CTP 中转模拟（休市联调）：模拟 tick 驱动 forming；
        # 分钟 forming 仍受交易时段闸门约束（休市只动日线）
        source_label = CHANNEL_CTP_SIM
    elif channel == CHANNEL_VVTR:
        # VVTR 需开关启用（镜像缺失/关闭时保持新浪 forming）
        from app.connectors.vvtr.config import read_vvtr_state_from_redis

        state = await read_vvtr_state_from_redis(redis_conn)
        if state.get("enabled") and state.get("quotes_enabled"):
            source_label = "vvtr"

    # bridge 写入的源键（channel_label = real_ctp / ctp_sim / vvtr / sina）
    quotes: list[dict[str, Any]] = []
    now_ts = time.time()
    try:
        async for key in redis_conn.scan_iter(f"market:live:source:{source_label}:*"):
            raw = await redis_conn.get(key)
            if not raw:
                continue
            try:
                q = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if not (isinstance(q, dict) and q.get("symbol") and q.get("last_price")):
                continue
            recv_ts = q.get("recv_ts")
            if (
                isinstance(recv_ts, (int, float))
                and now_ts - recv_ts > _SOURCE_STALE_SECONDS
            ):
                continue
            quotes.append(q)
    except Exception:
        logger.exception("读取 %s 行情失败", source_label)
        return []
    return quotes


async def _background_virtual_side_loop(redis_conn: aioredis.Redis) -> None:
    """虚拟盘 7×24：独立 1s 循环（脉冲陈旧价 + 盘口/成交 + K 线）

    与 live 新浪拉取解耦：周末 live 降到 60s 时 virtual 仍每秒更新，
    避免界面大量「像停盘」的冻结价。
    """
    fill_counter = 0
    while True:
        try:
            from app.services.kline_virtual import update_virtual_realtime_klines
            from app.services.market_channel import collect_quotes_from_redis
            from app.services.virtual_market_side import process_virtual_side_markets
            from app.services.virtual_quote_pulse import (
                pulse_stale_virtual_quotes,
                repair_drifted_virtual_quotes,
                trim_pulse_hist_bars,
            )

            # 约每 60s 补一次缺 key（openctp 从未推过的品种）
            fill_counter += 1
            if fill_counter >= 60:
                fill_counter = 0
                try:
                    from app.services.virtual_quote_fill import (
                        fill_missing_virtual_quotes_from_sina,
                    )

                    await fill_missing_virtual_quotes_from_sina(redis_conn)
                except Exception:
                    logger.exception("virtual 补行情失败")
                # 顺带清历史脏 bar（低频）
                try:
                    await trim_pulse_hist_bars(redis_conn)
                except Exception:
                    logger.exception("virtual 历史脏 bar 清理失败")

            # 先修漂移（随机游走残留），再锚价脉冲
            if fill_counter == 1 or fill_counter % 30 == 0:
                n_fix = await repair_drifted_virtual_quotes(redis_conn)
                if n_fix:
                    logger.info("virtual 漂移修复 %d", n_fix)
            await pulse_stale_virtual_quotes(redis_conn)
            virt_quotes = await collect_quotes_from_redis(redis_conn, "virtual")
            if virt_quotes:
                n_tr = await process_virtual_side_markets(redis_conn, virt_quotes)
                if n_tr:
                    logger.debug("virtual 成交推送 %d 笔", n_tr)
                await update_virtual_realtime_klines(redis_conn, virt_quotes)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("virtual 盘口/脉冲/K 线循环异常")
        await asyncio.sleep(_VIRTUAL_SIDE_INTERVAL)


async def _background_fetch_loop(redis_conn: aioredis.Redis) -> None:
    """后台循环：始终拉取行情，交易时段高频，非交易时段低频

    启动时立即拉取一次，保证页面始终有数据。
    交易时段：每秒拉取
    非交易时段：每 60 秒拉取，保持缓存存活供复盘分析
    """
    # 启动时立即拉取一次，无论是否交易时段
    try:
        quotes = await fetch_and_publish(redis_conn)
        await _process_quotes(redis_conn, quotes)
        logger.info("初始行情拉取完成，共 %d 条", len(quotes))
    except Exception:
        logger.error("初始行情拉取失败", exc_info=True)

    while True:
        trading = is_trading_hours()
        if trading:
            # 交易时段：按当前 active 渠道取刷新间隔（per-channel 覆盖默认）
            from app.core.trading_mode import MODE_LIVE
            from app.services.market_channel import read_channel_from_redis
            from app.services.refresh_cfg import get_refresh_interval

            channel = await read_channel_from_redis(redis_conn, MODE_LIVE)
            interval = await get_refresh_interval(redis_conn, channel)
        else:
            interval = _FETCH_INTERVAL_IDLE
        await asyncio.sleep(interval)
        try:
            quotes = await fetch_and_publish(redis_conn)
            await _process_quotes(redis_conn, quotes)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("行情拉取异常", exc_info=True)


async def _background_master_loop(redis_conn: aioredis.Redis) -> None:
    """主力合约识别循环:每 5 秒扫描 Redis quote 缓存,识别每个品种的主力/次主力

    启动时立即跑一次,然后每 _MASTER_INTERVAL 秒循环一次。
    失败不中断循环。
    """
    try:
        await cache_master_contracts(redis_conn)
        logger.info("初始主力合约识别完成")
    except Exception:
        logger.error("初始主力合约识别失败", exc_info=True)

    while True:
        await asyncio.sleep(_MASTER_INTERVAL)
        try:
            await cache_master_contracts(redis_conn)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("主力合约识别异常", exc_info=True)


async def _background_price_alert_loop(redis_conn: aioredis.Redis) -> None:
    """价格预警检测循环：每 _PRICE_ALERT_INTERVAL 秒扫 enabled 预警对比最新价。

    命中即写 trade 类通知并自动禁用该预警（避免刷屏）。仅持锁 master worker 运行。
    """
    # 启动稍延后，避免与初始行情拉取抢资源
    await asyncio.sleep(8)
    while True:
        try:
            if _session_factory is None:
                logger.warning("价格预警跳过：session_factory 未注入")
            else:
                from app.services.notify.price_alert_svc import detect_triggered

                async with _session_factory() as session:
                    n = await detect_triggered(redis_conn, session)
                    if n:
                        logger.info("价格预警命中 %d 条", n)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("价格预警检测异常", exc_info=True)
        await asyncio.sleep(_PRICE_ALERT_INTERVAL)


async def _background_big_order_purge_loop(redis_conn: aioredis.Redis) -> None:
    """逐秒成交清空循环：北京时间每日 20:30 清空上一交易日数据。

    交易日窗口：21:00 开盘 → 次日 15:00 收盘为同一交易日。
    20:30 介于收盘(15:00)与开盘(21:00)之间，此时清空上一交易日的 trade_tick，
    为当晚新交易日腾出空间。仅持锁 master worker 运行，避免多 worker 重复删除。
    """
    global _big_order_last_purge_day
    await asyncio.sleep(15)  # 启动稍延后
    while True:
        try:
            from app.services.trading_hours import beijing_now

            bj = beijing_now()
            today_key = bj.strftime("%Y-%m-%d")
            # 仅在 20:30~20:31 触发，且当天尚未清过
            if (
                bj.hour == _BIG_ORDER_PURGE_HOUR
                and bj.minute == 30
                and _big_order_last_purge_day != today_key
            ):
                _big_order_last_purge_day = today_key
                if _session_factory is not None:
                    from app.services.notify.trade_tick_svc import purge_before_day
                    from app.services.trading_hours import get_trading_day

                    # 今日 20:30 的归属交易日 = get_trading_day(now).date()
                    cutoff = get_trading_day(bj).date()
                    async with _session_factory() as session:
                        n = await purge_before_day(session, cutoff)
                        if n:
                            logger.info(
                                "trade_tick 20:30 清空完成，删除 %d 条（< %s）",
                                n,
                                cutoff,
                            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("trade_tick 清空异常")
        await asyncio.sleep(_BIG_ORDER_PURGE_INTERVAL)


# 新闻采集间隔（秒）
_NEWS_INTERVAL = 300  # 5 分钟


async def _background_news_loop(redis_conn: aioredis.Redis) -> None:
    """后台循环：每 5 分钟采集一次期货新闻"""
    # 启动时立即采集一次
    try:
        await collect_and_cache_news(redis_conn)
        logger.info("初始新闻采集完成")
    except Exception:
        logger.error("初始新闻采集失败", exc_info=True)

    while True:
        await asyncio.sleep(_NEWS_INTERVAL)
        try:
            await collect_and_cache_news(redis_conn)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("新闻采集异常", exc_info=True)


def _worker_id() -> str:
    """当前 worker 标识（主机名-pid）"""
    return f"{socket.gethostname()}-{os.getpid()}"


async def _try_acquire_lock(redis_conn: aioredis.Redis) -> bool:
    """尝试获取 scheduler 主锁

    使用 SET NX EX 实现:只有第一个 worker 能拿到锁,其余 worker 直接返回 False。
    """
    result = await redis_conn.set(
        _SCHEDULER_LOCK_KEY,
        _worker_id(),
        ex=_SCHEDULER_LOCK_TTL,
        nx=True,
    )
    return bool(result)


async def _release_lock(redis_conn: aioredis.Redis) -> None:
    """仅当本 worker 仍持有锁时释放"""
    try:
        await redis_conn.eval(
            _RELEASE_LOCK_LUA,
            1,
            _SCHEDULER_LOCK_KEY,
            _worker_id(),
        )
    except Exception:
        logger.error("释放 scheduler 主锁失败", exc_info=True)


async def _cancel_named_task(name: str, task: asyncio.Task | None) -> None:
    """取消并等待单个后台任务"""
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    logger.info("%s后台任务已关闭", name)


async def _cancel_data_tasks() -> None:
    """取消行情相关任务（不含锁续期 / watchdog）

    锁丢失时调用：必须先停写 Redis 的任务，再让本 worker 进入 watchdog。
    """
    global _bg_task, _news_task, _master_task, _ai_trading_task
    global _virtual_side_task, _price_alert_task, _big_order_purge_task
    global _channel_health_task, _channel_preflight_task
    global _kline_source_health_task, _kline_sync_task, _vvtr_bridge_task
    global _vvtr_tickdata_task
    global _contracts_sync_task
    global _kline_reconcile_task
    global _tickdata_backfill_task
    global _ai_anchor_task
    global _kline_prewarm_task
    await _cancel_named_task("行情拉取", _bg_task)
    await _cancel_named_task("新闻采集", _news_task)
    await _cancel_named_task("主力识别", _master_task)
    await _cancel_named_task("AI交易", _ai_trading_task)
    await _cancel_named_task("虚拟盘盘口", _virtual_side_task)
    await _cancel_named_task("价格预警", _price_alert_task)
    await _cancel_named_task("大单清空", _big_order_purge_task)
    await _cancel_named_task("渠道健康监控", _channel_health_task)
    await _cancel_named_task("渠道开盘前自检", _channel_preflight_task)
    await _cancel_named_task("K线源健康监控", _kline_source_health_task)
    await _cancel_named_task("K线TqSdk修正", _kline_sync_task)
    await _cancel_named_task("VVTR行情桥", _vvtr_bridge_task)
    await _cancel_named_task("VVTR tickdata同步", _vvtr_tickdata_task)
    await _cancel_named_task("VVTR主力合约同步", _contracts_sync_task)
    await _cancel_named_task("K线盘中对账", _kline_reconcile_task)
    await _cancel_named_task("tickdata缺口回填", _tickdata_backfill_task)
    await _cancel_named_task("AI看盘主播", _ai_anchor_task)
    await _cancel_named_task("K线盘前预热", _kline_prewarm_task)
    _bg_task = None
    _news_task = None
    _master_task = None
    _ai_trading_task = None
    _factor_mining_task = None
    _factor_health_task = None
    _virtual_side_task = None
    _price_alert_task = None
    _big_order_purge_task = None
    _channel_health_task = None
    _channel_preflight_task = None
    _kline_source_health_task = None
    _kline_sync_task = None
    _vvtr_bridge_task = None
    _vvtr_tickdata_task = None
    _contracts_sync_task = None
    _kline_reconcile_task = None
    _tickdata_backfill_task = None
    _ai_anchor_task = None
    _kline_prewarm_task = None


async def _handle_lock_lost(redis_conn: aioredis.Redis) -> None:
    """主锁丢失：停止本 worker 写任务，转入 watchdog 等待重新选主"""
    global _is_master, _lock_task
    logger.warning(
        "scheduler 主锁丢失(worker=%s)，停止后台写任务并转入 watchdog",
        _worker_id(),
    )
    _is_master = False
    await _cancel_data_tasks()
    # 续期循环即将退出，清空引用
    _lock_task = None
    # 避免重复挂多个 watchdog
    global _watchdog_task
    if _watchdog_task is None or _watchdog_task.done():
        _watchdog_task = asyncio.create_task(_wait_and_acquire_lock_loop(redis_conn))


async def _renew_lock_loop(redis_conn: aioredis.Redis) -> None:
    """定期原子续期 scheduler 主锁；续期失败则停写任务"""
    worker_id = _worker_id()
    while True:
        try:
            renewed = await redis_conn.eval(
                _RENEW_LOCK_LUA,
                1,
                _SCHEDULER_LOCK_KEY,
                worker_id,
                str(_SCHEDULER_LOCK_TTL),
            )
            if not renewed:
                await _handle_lock_lost(redis_conn)
                return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("scheduler 锁续期异常", exc_info=True)
        await asyncio.sleep(_SCHEDULER_LOCK_RENEW_INTERVAL)


async def _start_background_tasks(redis_conn: aioredis.Redis) -> None:
    """启动锁续期 + 后台任务(行情/新闻/主力/全月份/AI交易)

    抽成独立函数,供 start_scheduler 和 watchdog 接管时复用,避免重复代码。
    """
    global _bg_task, _news_task, _lock_task, _master_task
    global _ai_trading_task, _virtual_side_task, _price_alert_task
    global _factor_mining_task, _factor_health_task, _vvtr_bridge_task
    global _vvtr_tickdata_task
    # 冷启动：把 DB 里的刷新间隔灌入 Redis，保证 Redis 重启后恢复用户配置。
    # 放此处而非 start_scheduler，以覆盖 watchdog 接管路径。
    if _session_factory is not None:
        try:
            from app.services.refresh_cfg import write_refresh_intervals_to_redis
            from app.services.system_settings_svc import (
                ensure_system_settings,
                settings_to_dict,
            )

            async with _session_factory() as session:
                row = await ensure_system_settings(session)
                cfg = settings_to_dict(row)
                await write_refresh_intervals_to_redis(
                    redis_conn,
                    cfg.get("market_refresh_interval_sec", 1),
                    cfg.get("market_refresh_intervals"),
                )
        except Exception:
            logger.warning("冷启动灌入刷新间隔失败", exc_info=True)
    # 防止重复启动
    await _cancel_data_tasks()
    if _lock_task is not None and not _lock_task.done():
        await _cancel_named_task("锁续期", _lock_task)
        _lock_task = None

    _lock_task = asyncio.create_task(_renew_lock_loop(redis_conn))
    _bg_task = asyncio.create_task(_background_fetch_loop(redis_conn))
    _news_task = asyncio.create_task(_background_news_loop(redis_conn))
    _master_task = asyncio.create_task(_background_master_loop(redis_conn))
    _virtual_side_task = asyncio.create_task(
        _background_virtual_side_loop(redis_conn),
        name="virtual-side-loop",
    )
    logger.info("虚拟盘 7×24 盘口/脉冲任务已启动")
    _price_alert_task = asyncio.create_task(
        _background_price_alert_loop(redis_conn),
        name="price-alert-loop",
    )
    logger.info("价格预警检测任务已启动")
    _big_order_purge_task = asyncio.create_task(
        _background_big_order_purge_loop(redis_conn),
        name="big-order-purge-loop",
    )
    logger.info("大单记录 20:30 清空任务已启动")

    # 渠道健康监控 + 开盘前自检（主备切换 / bridge 自动重启）
    from app.services.channel_failover import (
        channel_health_monitor_loop,
        channel_preflight_loop,
    )

    _channel_health_task = asyncio.create_task(
        channel_health_monitor_loop(redis_conn, _session_factory),
        name="channel-health-loop",
    )
    _channel_preflight_task = asyncio.create_task(
        channel_preflight_loop(redis_conn, _session_factory),
        name="channel-preflight-loop",
    )
    logger.info("渠道健康监控 + 开盘前自检任务已启动")

    # K 线历史数据源健康监控（每 30s 探测主+备源，写 Redis 供管理后台展示）
    from app.services.kline_source_health import kline_source_health_monitor_loop

    _kline_source_health_task = asyncio.create_task(
        kline_source_health_monitor_loop(redis_conn, _session_factory),
        name="kline-source-health-loop",
    )
    logger.info("K 线源健康监控任务已启动")

    # K 线历史数据 VVTR 全周期权威修正（01:00 凌晨 + 15:05 闭盘后）
    from app.services.kline_sync import kline_sync_loop

    _kline_sync_task = asyncio.create_task(
        kline_sync_loop(redis_conn, _session_factory),
        name="kline-sync-loop",
    )
    logger.info("K 线 vvtr 定时修正任务已启动（北京时间 01:00 + 15:05）")

    # K 线盘前预热（交易日 08:40 起主力×全周期填缓存，冷合约切换毫秒级；
    # openspec 2026-08-25-kline-prewarm-source-hardening）
    global _kline_prewarm_task
    from app.services.kline_prewarm import kline_prewarm_loop

    _kline_prewarm_task = asyncio.create_task(
        kline_prewarm_loop(redis_conn),
        name="kline-prewarm-loop",
    )
    logger.info("K 线盘前预热任务已启动")

    # K 线盘中对账（仅交易时段，每 10 分钟一轮）：
    # 用 VVTR /kline/current 当根权威值校正 forming 桶，差异时 version
    # 递增 + kind=correction 推送，前端按 version 幂等覆盖收敛。
    global _kline_reconcile_task
    _kline_reconcile_task = asyncio.create_task(
        _kline_reconcile_loop(redis_conn),
        name="kline-reconcile-loop",
    )
    logger.info("K 线盘中对账任务已启动（交易时段每 %d 分钟）", _KLINE_RECONCILE_INTERVAL // 60)

    # 升级后 K 线恢复（一次性）：启动 2 分钟后治愈恢复态 forming（当根权威
    # OHLC 替换）。K 线权威修正不在启动触发——只在 kline_sync_loop 每日
    # 01:00 / 15:05 定时点执行（容器每次重建都全量修正会压垮 vvtr 源与 PG）。
    global _post_deploy_task
    _post_deploy_task = asyncio.create_task(
        _post_deploy_reconcile_once(redis_conn),
        name="post-deploy-kline-reconcile",
    )

    # VVTR WS v2 深度行情桥（仅 master 单实例，满足每 key 1 连接约束）
    try:
        from app.connectors.vvtr.ws_bridge import vvtr_ws_bridge_loop

        _vvtr_bridge_task = asyncio.create_task(
            vvtr_ws_bridge_loop(redis_conn, _session_factory),
            name="vvtr-ws-bridge",
        )
        logger.info("VVTR WS 行情桥任务已启动（未配置密钥时自动休眠）")
    except Exception:
        logger.exception("启动 VVTR WS 行情桥失败")


    # VVTR K线 → pg-tickdata 每日自动同步（盘中每小时，休市 6 小时对账）
    try:
        from app.services.vvtr_tickdata_sync import vvtr_tickdata_sync_loop

        _vvtr_tickdata_task = asyncio.create_task(
            vvtr_tickdata_sync_loop(redis_conn, _session_factory),
            name="vvtr-tickdata-sync",
        )
        logger.info("VVTR K线 tickdata 同步任务已启动")
    except Exception:
        logger.exception("启动 VVTR tickdata 同步失败")

    # tickdata 缺口定向回填（每日 03:30，非主力合约缺段闭环）
    try:
        from app.services.tickdata_gap_backfill import tickdata_gap_backfill_loop

        global _tickdata_backfill_task
        _tickdata_backfill_task = asyncio.create_task(
            tickdata_gap_backfill_loop(redis_conn, _session_factory),
            name="tickdata-gap-backfill",
        )
        logger.info("tickdata 缺口回填任务已启动（每日 03:30）")
    except Exception:
        logger.exception("启动 tickdata 缺口回填失败")

    # VVTR 主力合约同步（realSymbol → 刷新 ALL_CONTRACTS；仅 master）
    try:
        from app.services.contracts_sync import (
            REFRESH_INTERVAL as _CS_REFRESH,
            refresh_masters_from_vvtr,
        )

        async def _contracts_sync_loop() -> None:
            # 启动先套用 Redis 快照（跨部署存活，秒级恢复主力），
            # 再拉 realSymbol 全量刷新（约 69 品种 × 1.1s 节流）
            from app.services.contracts_sync import ensure_active_contracts

            await ensure_active_contracts(redis_conn)
            while True:
                try:
                    await refresh_masters_from_vvtr(redis_conn, _session_factory)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("VVTR 主力合约同步失败")
                await asyncio.sleep(_CS_REFRESH)

        _contracts_sync_task = asyncio.create_task(
            _contracts_sync_loop(), name="contracts-sync-loop"
        )
        logger.info("VVTR 主力合约同步任务已启动（%dh 一刷）", _CS_REFRESH // 3600)
    except Exception:
        logger.exception("启动 VVTR 主力合约同步失败")

    # AI 交易自治 loop（与行情同 master worker）
    try:
        from app.services.ai_trading.loop import ai_trading_loop

        _ai_trading_task = asyncio.create_task(
            ai_trading_loop(_session_factory, redis_conn),
            name="ai-trading-loop",
        )
        logger.info("AI 交易后台任务已启动")
    except Exception:
        logger.exception("启动 AI 交易后台任务失败")

    # AI 看盘主播 loop（到期分析播报，同 master 选主）
    try:
        from app.services.ai_anchor.loop import ai_anchor_loop

        _ai_anchor_task = asyncio.create_task(
            ai_anchor_loop(_session_factory, redis_conn),
            name="ai-anchor-loop",
        )
        logger.info("AI 看盘主播后台任务已启动")
    except Exception:
        logger.exception("启动 AI 看盘主播后台任务失败")

    # 超级因子挖掘 loop（长程后台任务，复用同一 master worker 的选主机制）
    try:
        from app.services.factor_mining.loop import factor_mining_loop

        _factor_mining_task = asyncio.create_task(
            factor_mining_loop(_session_factory, redis_conn),
            name="factor-mining-loop",
        )
        logger.info("超级因子挖掘后台任务已启动")
    except Exception:
        logger.exception("启动超级因子挖掘后台任务失败")

    # 因子衰减监控 loop（每日刷新收藏因子 health，IC 口径，同 master 选主）
    try:
        from app.services.factor_lab.persist.health_loop import factor_health_loop

        _factor_health_task = asyncio.create_task(
            factor_health_loop(_session_factory, redis_conn),
            name="factor-health-loop",
        )
        logger.info("因子衰减监控后台任务已启动")
    except Exception:
        logger.exception("启动因子衰减监控后台任务失败")



async def _wait_and_acquire_lock_loop(redis_conn: aioredis.Redis) -> None:
    """watchdog: 未拿到锁的 worker 启动此循环,定期尝试接管

    每 _SCHEDULER_LOCK_RENEW_INTERVAL 秒重试一次 SET NX。
    当原 master worker 死亡/重启且 Redis 锁 TTL(20s) 过期后,
    本 worker(或并行的其他 worker)能拿到锁并启动后台任务。

    设计目的:避免"重启时所有 worker 都因残留锁拿不到主锁、后台任务永久停摆"的故障。
    SET NX 原子性保证多个 watchdog 同时尝试时只有一个成功。
    """
    global _is_master
    while True:
        try:
            await asyncio.sleep(_SCHEDULER_LOCK_RENEW_INTERVAL)
            got_lock = await _try_acquire_lock(redis_conn)
            if got_lock:
                _is_master = True
                logger.warning(
                    "watchdog 接管 scheduler 主锁(worker=%s),启动后台任务",
                    _worker_id(),
                )
                await _start_background_tasks(redis_conn)
                logger.info("行情拉取后台任务已启动(watchdog 接管)")
                return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("watchdog 接管异常", exc_info=True)


async def _kline_reconcile_loop(redis_conn: aioredis.Redis) -> None:
    """盘中对账循环：交易时段每 _KLINE_RECONCILE_INTERVAL 秒校一次 forming。

    非交易时段休眠跳过（无 forming 可校）；拉取/比对失败不中断循环。
    管理端可经 kline:task:kline_reconcile:paused 暂停（状态照常上报）。
    """
    from app.services.kline_task_status import (
        is_paused,
        log as task_log,
        report as task_report,
    )

    while True:
        try:
            await asyncio.sleep(_KLINE_RECONCILE_INTERVAL)
            if await is_paused(redis_conn, "kline_reconcile"):
                await task_report(redis_conn, "kline_reconcile", paused=True)
                continue
            if not is_trading_hours():
                continue
            from app.services.kline import reconcile_forming_with_vvtr

            n = await reconcile_forming_with_vvtr(redis_conn)
            if n:
                logger.info("K 线盘中对账：修正 %d 个 forming 桶", n)
            await task_report(
                redis_conn, "kline_reconcile",
                paused=False, last_corrected=n, last_error=None,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("K 线盘中对账异常")
            await task_report(redis_conn, "kline_reconcile", last_error=str(exc))


async def _post_deploy_reconcile_once(redis_conn: aioredis.Redis) -> None:
    """升级后一次性 K 线恢复（仅 master scheduler 启动时触发）

    只做重启恢复语义的轻量步骤（治愈恢复态 forming 桶）。
    K 线权威修正（sync_kline_from_tickdata）与历史缓存清理**不在此触发**：
    每次容器重建（33 每次合并 master 自动部署）都全量修正会给
    vvtr 分发源与 PG 带来巨大压力——修正只在 kline_sync_loop 的
    每日 01:00 / 15:05 定时点执行。
    """
    from app.services.kline_task_status import (
        log as task_log,
        report as task_report,
    )

    async def _begin() -> None:
        await asyncio.sleep(120)  # 等行情/forming/主锁稳定
        await task_report(redis_conn, "post_deploy_reconcile", running=True)

    steps: dict[str, Any] = {}
    try:
        from app.services.kline import heal_recovered_forming

        await _begin()
        healed = await heal_recovered_forming(redis_conn)
        steps["healed_forming"] = healed
        logger.info("升级后恢复：恢复态 forming 治愈 %d 个桶", healed)
    except Exception:
        logger.exception("升级后恢复：恢复态 forming 治愈失败")
    await task_report(
        redis_conn, "post_deploy_reconcile", running=False, last_stats=steps
    )
    await task_log(redis_conn, "post_deploy_reconcile", "info", "升级后恢复完成", **{
        k: v for k, v in steps.items() if not isinstance(v, dict)
    })


async def start_scheduler(redis_conn: aioredis.Redis) -> None:
    """启动后台行情拉取任务

    多 worker 模式下:
    - startup 时尝试拿 _SCHEDULER_LOCK_KEY,拿到锁的 worker 立即启动后台任务
    - 未拿到锁的 worker 启动 watchdog(_wait_and_acquire_lock_loop),
      当原 master 死亡且 Redis 锁过期后能接管,避免后台任务永久停摆
    """
    global _is_master, _watchdog_task

    try:
        await refresh_contracts_cache(redis_conn)
    except Exception:
        logger.error("合约缓存初始化失败，将在后台重试", exc_info=True)

    # 冷启动：若 Redis 尚无 market:channel，用 DB/默认 sina 写入
    try:
        from app.services.market_channel import (
            CHANNEL_SINA,
            REDIS_CHANNEL_KEY,
            write_channel_to_redis,
        )

        existing = await redis_conn.get(REDIS_CHANNEL_KEY)
        if existing is None:
            channel = CHANNEL_SINA
            if _session_factory is not None:
                try:
                    from app.services.system_settings_svc import (
                        ensure_system_settings,
                        settings_to_dict,
                    )

                    async with _session_factory() as session:
                        row = await ensure_system_settings(session)
                        await session.commit()
                        channel = str(
                            settings_to_dict(row).get("market_data_channel")
                            or CHANNEL_SINA
                        )
                except Exception:
                    logger.warning("从 DB 读取行情渠道失败，默认 sina", exc_info=True)
            await write_channel_to_redis(redis_conn, channel)
            logger.info("行情渠道镜像已初始化: %s", channel)
        else:
            # 已有渠道：补写 live/virtual 镜像键
            await write_channel_to_redis(
                redis_conn,
                existing.decode() if isinstance(existing, bytes) else str(existing),
            )
    except Exception:
        logger.warning("初始化 market:channel 失败", exc_info=True)

    # 迁移期：旧 market:quote:* → market:live:quote:*（仅补缺，不覆盖）
    try:
        await _bootstrap_live_namespace_from_legacy(redis_conn)
    except Exception:
        logger.warning("live 命名空间引导失败", exc_info=True)

    got_lock = await _try_acquire_lock(redis_conn)
    if not got_lock:
        logger.info("未获得 scheduler 主锁(其他 worker 已持有),启动 watchdog 等待接管")
        _watchdog_task = asyncio.create_task(_wait_and_acquire_lock_loop(redis_conn))
        return

    _is_master = True
    logger.info("已获得 scheduler 主锁(worker=%s),启动后台任务", _worker_id())
    await _start_background_tasks(redis_conn)
    logger.info("行情拉取后台任务已启动")


async def stop_scheduler() -> None:
    """优雅关闭后台任务并释放主锁"""
    global _bg_task, _news_task, _lock_task, _master_task
    global _ai_trading_task, _watchdog_task, _is_master

    await _cancel_data_tasks()
    await _cancel_named_task("锁续期", _lock_task)
    await _cancel_named_task("watchdog", _watchdog_task)
    _lock_task = None
    _watchdog_task = None
    _ai_trading_task = None

    # 尝试释放 Redis 锁（仅持有者生效）；无 redis 连接时跳过
    # stop_scheduler 签名未带 redis，由 lifespan 在 close redis 前调用；
    # 锁 TTL 会自然过期，这里不再强依赖 redis 实例。
    _is_master = False
