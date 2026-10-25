"""VVTR 行情桥 —— scheduler master 内单实例运行

通道策略（按套餐权限自适应，订阅运行时合约表，上限 100）：
1. v2 深度行情 WS（type=14，250~500ms 推送）：有权限时的主通道；
2. v1 L1 WS（约 1s 一批，每批多合约）：v2 被拒（403）时的主通道；
   交易时段 120s 零推送则停测降级（30 分钟冷却）；
3. HTTP /snapshot 稳态轮询：3 秒/轮主力合批（约 0.33 req/s，
   遵守 1 req/s 限频）；
每 10 分钟重试 v2 —— 套餐升级后自动升回深度推送，无需重启。

其余职责：
- 推送/快照实时合入内存 latest，每 1s 批量落 Redis
  market:live:source:vvtr:quote:{sym}（TTL 7d），下游 merge → 统一
  quote/盘口/K线 forming/WS 推送全复用；
- 每 45s 文本 "ping" 保活；心跳键 market:live:vvtr:heartbeat 供健康探测；
- WS 会话内配置监视（5s）：关开关/换密钥即时断开重连，
  释放「每 key 仅 1 连接」名额；
- 每 10s 热加载配置（管理端改 apiKey/开关后自动生效）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from typing import Any

import redis.asyncio as aioredis

from app.connectors.vvtr.config import (
    VvtrRuntimeConfig,
    cap_symbol_list,
    get_vvtr_runtime_config,
    write_vvtr_state_to_redis,
)
from app.connectors.vvtr.quote_map import (
    parse_vvtr_frames,
    vvtr_v1_message_to_quote,
    vvtr_ws_message_to_quote,
)
from app.services.market_keys import source_quote_key

logger = logging.getLogger("qihuo.vvtr.ws_bridge")

# 心跳键（渠道健康探测读取；与 CTP bridge 心跳模式一致但独立命名空间）
VVTR_HEARTBEAT_KEY = "market:live:vvtr:heartbeat"
_HEARTBEAT_TTL = 120
_HEARTBEAT_INTERVAL = 10

# 内存合流 → Redis 批量落盘间隔（秒）＝对下游的秒级供给节奏
_FLUSH_INTERVAL = 1.0

# 文本保活间隔（官方要求每分钟内发送）
_PING_INTERVAL = 45

# quote 源键 TTL（与 CTP bridge 一致：7 天）
_QUOTE_TTL = 7 * 24 * 3600

# 断线重连退避（秒）
_BACKOFF_INITIAL = 1.0
_BACKOFF_MAX = 60.0

# WS 断连时的 HTTP 兜底轮询间隔（3s 一轮全市场合批 ≈ 0.33 req/s）
_HTTP_FALLBACK_INTERVAL = 3.0

# 稳态 HTTP 模式下定期重试 v2 的间隔（套餐升级后自动升回深度推送）
_V2_RETRY_INTERVAL = 600.0

# 涨跌幅基准(昨收)缓存寿命(秒):品种日线收盘日内不变,6h 足够
_PREV_CLOSE_TTL = 6 * 3600.0

# v1 零推送停测阈值：交易时段连接超过此时长无任何 tick 视为订阅失效
_WS_STALL_TIMEOUT = 120.0
# WS 会话内配置监视间隔（关开关/换密钥即时生效）
_CONFIG_WATCH_INTERVAL = 5.0

# 配置热加载间隔
_CONFIG_RELOAD_INTERVAL = 10.0


def _now() -> float:
    return time.time()


class VvtrWsBridge:
    """单连接 WS 桥（仅 scheduler master 持有一个实例）"""

    def __init__(self, redis_conn: aioredis.Redis, session_factory: Any) -> None:
        self._redis = redis_conn
        self._session_factory = session_factory
        self._latest: dict[str, dict[str, Any]] = {}
        self._latest_lock = asyncio.Lock()
        self._ws: Any = None
        self._mode = "idle"  # idle / ws_v2 / ws_v1 / http
        self._ws_version = 2  # 当前会话 WS 版本（决定消息映射器）
        self._connected = False
        self._subscribed_symbols = 0
        self._last_flush_ts = 0.0
        self._last_msg_ts: float | None = None
        self._session_start_ts: float = 0.0
        self._stalled = False
        self._v1_task: asyncio.Task | None = None
        self._stop = False
        # 涨跌幅基准:品种 → 昨收(6h 刷新);_prev_close_at=0 启动即加载
        self._prev_close: dict[str, float] = {}
        self._prev_close_at: float = 0.0

    # ---------- 心跳/状态 ----------

    async def _write_heartbeat(self) -> None:
        payload = {
            "ts": _now(),
            "channel": "vvtr",
            "mode": self._mode,
            "connected": self._connected,
            "symbols": self._subscribed_symbols,
        }
        with contextlib.suppress(Exception):
            await self._redis.set(
                VVTR_HEARTBEAT_KEY, json.dumps(payload), ex=_HEARTBEAT_TTL
            )

    # ---------- 落盘 ----------

    async def _flush_latest(self) -> int:
        """内存 latest → Redis 源键（pipeline 批量写；统一盖 recv_ts 时间戳）

        VVTR v1/深度帧不带昨收/昨结算基准 → normalize 后 change/pct 为
        None、pre_close=None;用品种上一交易日收盘价兜底(进程内缓存,6h
        刷新)恢复涨跌幅近似显示(2026-08-24 全市场 0.00% 事故的根治点;
        v2 帧路径的准确昨结基准在 quote_map 修正为 pre_settle)。
        """
        async with self._latest_lock:
            if not self._latest:
                return 0
            pending = self._latest
            self._latest = {}
        if self._prev_close_at + _PREV_CLOSE_TTL < _now():
            await self._refresh_prev_close_map()
        ts = _now()
        # v1 帧缺基准的合约：优先取同合约 sina 源的昨结（合约级真值，
        # sina-bridge 同机双写本 Redis），缺失才用品种上一交易日收盘近似
        # ——品种指数收盘与具体合约昨结常有偏差（曾致休市期百分比失真）。
        need_symbols = [s for s, q in pending.items() if not q.get("pre_close")]
        sina_pres: dict[str, float] = {}
        if need_symbols:
            raw_values = await self._redis.mget(
                [source_quote_key("sina", s) for s in need_symbols]
            )
            for sym, raw in zip(need_symbols, raw_values):
                if not raw:
                    continue
                try:
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", errors="ignore")
                    data = json.loads(raw)
                    pre = float(data.get("pre_close") or 0) if isinstance(data, dict) else 0.0
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
                if pre > 0:
                    sina_pres[sym] = pre
        pipe = self._redis.pipeline(transaction=False)
        for symbol, quote in pending.items():
            quote["recv_ts"] = ts
            if not quote.get("pre_close"):
                code = symbol.rstrip("0123456789")
                prev = sina_pres.get(symbol) or self._prev_close.get(code)
                if prev and prev > 0:
                    last = float(quote.get("last_price") or 0)
                    quote["pre_close"] = prev
                    if last > 0:
                        quote["change"] = round(last - prev, 10)
                        quote["change_pct"] = round((last - prev) / prev * 100, 4)
            pipe.set(
                source_quote_key("vvtr", symbol),
                json.dumps(quote, ensure_ascii=False),
                ex=_QUOTE_TTL,
            )
        with contextlib.suppress(Exception):
            await pipe.execute()
        return len(pending)

    async def _refresh_prev_close_map(self) -> None:
        """品种 → 最近已完成交易日收盘价(主 PG kline_history);失败静默留旧值

        必须排除当日(trade_date < CURRENT_DATE)：当日行盘中写入时
        close≈last，作基准会把涨跌幅算成 ≈0%。
        """
        self._prev_close_at = _now()
        if self._session_factory is None:
            return
        try:
            from sqlalchemy import text

            async with self._session_factory() as session:
                rows = await session.execute(
                    text(
                        "SELECT DISTINCT ON (code) code, close "
                        "FROM kline_history "
                        "WHERE trade_date < CURRENT_DATE "
                        "ORDER BY code, trade_date DESC"
                    )
                )
                mapping = {r[0]: float(r[1]) for r in rows if r[1]}
            if mapping:
                self._prev_close = mapping
                logger.info("涨跌幅基准(昨收兜底)已加载 %d 品种", len(mapping))
        except Exception:
            logger.debug("昨收基准加载失败,沿用旧值", exc_info=True)

    async def _flush_loop(self) -> None:
        while not self._stop:
            await asyncio.sleep(_FLUSH_INTERVAL)
            try:
                n = await self._flush_latest()
                self._last_flush_ts = _now()
                if n:
                    logger.debug("VVTR flush %d quotes", n)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("VVTR flush 失败")

    async def _heartbeat_loop(self) -> None:
        while not self._stop:
            await self._write_heartbeat()
            await self._refresh_state_mirror()
            await self._check_ws_stall()
            await asyncio.sleep(_HEARTBEAT_INTERVAL)

    async def _refresh_state_mirror(self) -> None:
        """刷新 market:vvtr:state 开关镜像（TTL 300s）

        v1/v2 会话供数时主循环阻塞在 await 会话任务上，外层循环
        不再迭代 → 镜像 5 分钟后过期，下游（merge/K线 forming）把
        VVTR 视为关闭、静默切回新浪。挂到常驻心跳（10s）保证任何
        会话路径下镜像始终存活。
        """
        try:
            cfg = await get_vvtr_runtime_config(self._session_factory)
            await write_vvtr_state_to_redis(
                self._redis,
                enabled=cfg.enabled,
                quotes_enabled=cfg.quotes_enabled,
                kline_enabled=cfg.kline_enabled,
                ws_enabled=cfg.ws_enabled,
            )
        except Exception:
            logger.debug("VVTR 状态镜像刷新失败", exc_info=True)

    async def _check_ws_stall(self) -> None:
        """交易时段零推送停测（挂在心跳循环里执行，确保必然运行）

        连接存活但长时间无任何行情帧（含完全静默的连接）则主动断开，
        由 supervisor 降级 HTTP 稳态轮询。休市时段无推送属正常，不触发。
        停测判定同时保留独立 watchdog 任务（双保险，幂等）。
        """
        ws = self._ws
        if ws is None or self._mode not in ("ws_v1", "ws_v2"):
            return
        try:
            from app.services.trading_hours import is_trading_hours

            if not is_trading_hours():
                return
        except Exception:
            pass
        baseline = (
            self._last_msg_ts
            if self._last_msg_ts is not None
            else self._session_start_ts
        )
        if not baseline or time.monotonic() - baseline <= _WS_STALL_TIMEOUT:
            return
        if self._stalled:
            return  # 已触发过，等待会话退出
        self._stalled = True
        logger.warning(
            "VVTR WS(v%d) 交易时段 %.0fs 无任何推送，判定订阅失效，降级 HTTP",
            self._ws_version,
            _WS_STALL_TIMEOUT,
        )
        with contextlib.suppress(Exception):
            await ws.close()

    # ---------- 消息处理 ----------

    async def _on_message(self, raw: str | bytes) -> None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        text = raw.strip()
        if not text or text.startswith("pong"):
            return
        self._last_msg_ts = time.monotonic()
        # v1 推送为双重 JSON 编码（外层元素是字符串），parse_vvtr_frames
        # 统一剥层；只解外层会把每个元素当字符串丢给映射器 → 全部丢弃。
        frames = parse_vvtr_frames(text)
        if not frames:
            return
        mapper = (
            vvtr_v1_message_to_quote
            if self._ws_version == 1
            else vvtr_ws_message_to_quote
        )
        from app.data.quote_schema import normalize_quote_fields

        hit = 0
        for msg in frames:
            quote = mapper(msg)
            if quote is None:
                continue
            # 统一契约规范化（quote_map 头注释声明但此前未落实）：类型防御
            # + tick_time_iso 归一，使主源与 CTP/新浪同口径
            nq = normalize_quote_fields(quote)
            if nq is None:
                continue
            async with self._latest_lock:
                self._latest[str(nq["symbol"])] = nq
            hit += 1
        if hit:
            self._subscribed_symbols = max(self._subscribed_symbols, hit)

    # ---------- WS 会话 ----------

    @staticmethod
    def _ws_url_for(cfg: VvtrRuntimeConfig, version: int) -> str:
        """按版本解析 WS 地址：v2 用配置原值；v1 由 v2 地址推导（或默认）"""
        base = cfg.ws_url
        if version == 2:
            return base
        if "/v2/" in base:
            return base.replace("/v2/", "/v1/")
        return "wss://rest.vvtr.com/v1/connect"

    async def _master_symbols(self) -> list[str]:
        """运行时合约表（VVTR 主力同步/迁移后）∩ VVTR 有效清单（上限 100）

        订阅与消费端同源（ALL_CONTRACTS 经 contracts_sync 刷新/迁移），
        避免表已迁到下一主力而桥仍订旧主力导致前台拿不到 WSS 数据。
        只订阅真实月份合约（9999/9998/8888 合成主力由 cap_symbol_list
        剔除）。VVTR /symbols 清单过滤已下市合约：订阅中混入任一无效
        合约会导致整个 WSS 订阅零推送。
        """
        from app.services.contracts_sync import ensure_active_contracts

        await ensure_active_contracts(self._redis)

        from app.data.contracts import ALL_CONTRACTS

        symbols = [
            str(c.get("symbol") or "").strip()
            for c in ALL_CONTRACTS
            if str(c.get("symbol") or "").strip()
        ]
        # 与 VVTR 有效合约清单求交集（10 分钟缓存；失败回退原列表）
        valid = await self._vvtr_valid_symbols()
        if valid:
            from app.connectors.vvtr.config import match_valid_vvtr_symbols

            matched = match_valid_vvtr_symbols(symbols, valid)
            if matched:
                dropped = len(symbols) - len(matched)
                if dropped:
                    logger.info(
                        "VVTR 订阅清单过滤：合约 %d 个，%d 个不在有效清单（已剔除）",
                        len(symbols),
                        dropped,
                    )
                return cap_symbol_list(matched)
            logger.warning(
                "VVTR 有效清单交集为空（清单 %d 个），回退未过滤主力列表",
                len(valid),
            )
        return cap_symbol_list([str(s or "").strip().upper() for s in symbols])

    async def _vvtr_valid_symbols(self) -> list[str]:
        """VVTR /symbols 有效合约清单（进程内缓存 10 分钟）"""
        now = time.monotonic()
        cached = getattr(self, "_valid_symbols_cache", None)
        if cached and now - cached[0] < 600.0:
            return cached[1]
        try:
            cfg = await get_vvtr_runtime_config(self._session_factory)
            if not cfg.api_key:
                return []
            from asyncio import to_thread

            from app.connectors.vvtr.http_client import fetch_vvtr_symbols

            valid = await to_thread(
                fetch_vvtr_symbols, cfg.api_key, cfg.base_url
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("VVTR /symbols 拉取失败", exc_info=True)
            return cached[1] if cached else []
        self._valid_symbols_cache = (now, valid)
        return valid

    async def _config_watchdog(self, ws: Any) -> None:
        """WS 会话内配置监视：关开关/换密钥/关 WSS 后立即断开当前连接

        修复：supervisor 阻塞在长连会话中时无法热加载配置，
        关闭总开关后旧连接持续占用「每 key 1 连接」名额。
        """
        while not self._stop:
            await asyncio.sleep(_CONFIG_WATCH_INTERVAL)
            try:
                cfg = await get_vvtr_runtime_config(self._session_factory)
                if not cfg.ws_active or not cfg.ws_enabled:
                    logger.info("VVTR 配置已关闭（或 WSS 停用），主动断开 WS 会话")
                    with contextlib.suppress(Exception):
                        await ws.close()
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("VVTR 配置监视读取失败", exc_info=True)

    async def _stall_watchdog(self, ws: Any) -> None:
        """停测独立 watchdog（双保险；主判定在心跳循环的 _check_ws_stall）"""
        while not self._stop:
            await asyncio.sleep(15)
            await self._check_ws_stall()

    async def _run_ws_session(
        self, cfg: VvtrRuntimeConfig, version: int, symbols: list[str]
    ) -> str:
        """单次 WS 连接生命周期；返回 disconnected / forbidden（403 无权限）/
        stalled（交易时段零推送）"""
        import websockets

        # 经 relay 时用 RELAY_TOKEN 作下游凭证（relay 剥钥后注入容器真钥）；
        # 直连官方仍用真钥。未配置 token 保持原值（relay 兼容放行）。
        api_key = cfg.api_key
        relay_token = os.getenv("VVTR_RELAY_TOKEN", "").strip()
        if relay_token:
            from app.connectors.vvtr.http_client import throttle_applies

            if not throttle_applies(cfg.ws_url):
                api_key = relay_token
        params = f"apiKey={api_key}&type=14&symbols={','.join(symbols)}"
        url = f"{self._ws_url_for(cfg, version)}?{params}"
        self._ws_version = version
        self._stalled = False
        self._last_msg_ts = None
        self._session_start_ts = time.monotonic()
        self._mode = "ws_v2" if version == 2 else "ws_v1"
        logger.info(
            "VVTR WS(v%d) 连接中…（订阅 %d 个主力合约）", version, len(symbols)
        )
        try:
            async with websockets.connect(
                url,
                max_size=8 * 1024 * 1024,
                ping_interval=50,
                ping_timeout=20,
            ) as ws:
                self._ws = ws
                self._connected = True
                logger.info("VVTR WS(v%d) 已连接", version)
                ping_task = asyncio.create_task(self._ping_loop(ws))
                watch_task = asyncio.create_task(self._config_watchdog(ws))
                stall_task = asyncio.create_task(self._stall_watchdog(ws))
                try:
                    async for message in ws:
                        await self._on_message(message)
                finally:
                    for task in (ping_task, watch_task, stall_task):
                        task.cancel()
                    for task in (ping_task, watch_task, stall_task):
                        with contextlib.suppress(asyncio.CancelledError):
                            await task
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            text = str(exc)
            logger.warning("VVTR WS(v%d) 断开：%s", version, exc)
            # 403 = 密钥无该通道权限（如未购买深度行情），返回降级信号
            if "403" in text or "Forbidden" in text:
                return "forbidden"
        finally:
            self._ws = None
            self._connected = False
        if self._stalled:
            return "stalled"
        return "disconnected"

    async def _ping_loop(self, ws: Any) -> None:
        while not self._stop:
            await asyncio.sleep(_PING_INTERVAL)
            with contextlib.suppress(Exception):
                await ws.send("ping")

    # ---------- HTTP 兜底 ----------

    async def _http_fallback_once(self, cfg: VvtrRuntimeConfig) -> int:
        """WS 不可用时的快照兜底：主力合约合批单请求 → 内存 latest"""
        from asyncio import to_thread

        from app.connectors.vvtr.http_client import fetch_vvtr_snapshot

        symbols = await self._master_symbols()
        try:
            quotes = await to_thread(
                fetch_vvtr_snapshot, cfg.api_key, symbols, cfg.base_url
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("VVTR HTTP 兜底失败")
            return 0
        for quote in quotes:
            # HTTP 兜底快照同走统一契约（类型防御 + tick_time_iso）
            from app.data.quote_schema import normalize_quote_fields

            nq = normalize_quote_fields(quote)
            if nq is None:
                continue
            async with self._latest_lock:
                self._latest[str(nq["symbol"])] = nq
        return len(quotes)

    # ---------- 主循环 ----------

    async def run(self) -> None:
        """supervisor：配置热加载 + v2→v1→HTTP 降级链 + 定期重试

        订阅范围：运行时合约表（contracts_sync 主力同步/迁移后的清单，
        9999 等合成主力不订阅），上限 100（官方单连接订阅上限）。

        降级链：v2 深度（250~500ms，套餐升级后自动启用）→ v1 L1（约
        3s 推送；交易时段 120s 零推送则停测降级，30 分钟冷却）→
        HTTP /snapshot 稳态轮询（3s/轮，约 0.33 req/s 守 1 rps 限频）。
        稳态模式每 10 分钟重试 v2。
        """
        backoff = _BACKOFF_INITIAL
        helpers: list[asyncio.Task] = []
        try:
            helpers = [
                asyncio.create_task(self._flush_loop(), name="vvtr-flush"),
                asyncio.create_task(self._heartbeat_loop(), name="vvtr-heartbeat"),
            ]
            while not self._stop:
                try:
                    cfg = await get_vvtr_runtime_config(self._session_factory)
                    await write_vvtr_state_to_redis(
                        self._redis,
                        enabled=cfg.enabled,
                        quotes_enabled=cfg.quotes_enabled,
                        kline_enabled=cfg.kline_enabled,
                        ws_enabled=cfg.ws_enabled,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("VVTR 配置读取失败")
                    cfg = VvtrRuntimeConfig()

                if not cfg.ws_active:
                    if self._connected or self._mode != "idle":
                        logger.info("VVTR 未启用或未配置密钥，桥接休眠")
                    self._mode = "idle"
                    self._connected = False
                    self._subscribed_symbols = 0
                    backoff = _BACKOFF_INITIAL
                    await asyncio.sleep(5)
                    continue

                # ---------- 通道编排（确定性供数） ----------
                # v1 会话与 HTTP 稳态并行：v1 出推送即接管（暂停 HTTP），
                # v1 静默则 HTTP 持续供数；任何时刻至少一条通道在出数据。
                v1_alive = self._v1_task is not None and not self._v1_task.done()
                if v1_alive and self._subscribed_symbols > 0 and cfg.ws_enabled:
                    # v1 正在实际供数：阻塞等待其结束（配置监视负责按时关闭）
                    self._mode = "ws_v1"
                    await self._v1_task
                    self._v1_task = None
                    if self._stop:
                        break
                    continue
                if v1_alive:
                    # v1 静默存活（或 WSS 已被关闭）：释放连接名额
                    self._v1_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await self._v1_task
                    self._v1_task = None

                if not cfg.ws_enabled:
                    # WSS 推送关闭（后台开关）：纯 HTTP 快照稳态轮询
                    if self._mode != "http":
                        logger.info("VVTR WSS 推送已关闭，纯 HTTP 快照轮询")
                    await self._http_steady_loop(cfg)
                    continue

                self._subscribed_symbols = 0
                masters = await self._master_symbols()
                outcome = await self._run_ws_session(cfg, 2, masters)
                if self._stop:
                    break

                if outcome == "forbidden":
                    logger.warning(
                        "VVTR v2 深度行情被拒（403，套餐未含深度），"
                        "v1 L1 并行尝试 + HTTP 快照稳态供数"
                    )
                    backoff = _BACKOFF_INITIAL
                    self._subscribed_symbols = 0
                    self._v1_task = asyncio.create_task(
                        self._run_ws_session(cfg, 1, masters),
                        name="vvtr-ws-v1",
                    )
                    # HTTP 稳态供数（v1 出推送会自动返回让位）
                    await self._http_steady_loop(cfg)
                    continue

                # v2 普通断开：退避期间 HTTP 兜底后重试
                wait = backoff
                backoff = min(backoff * 2, _BACKOFF_MAX)
                logger.info(
                    "VVTR WS 断连，%.0fs 后重连（期间 HTTP 兜底）", wait
                )
                fallback = asyncio.create_task(
                    self._http_fallback_session(cfg, wait)
                )
                await asyncio.sleep(wait)
                fallback.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await fallback
        finally:
            self._stop = True
            self._connected = False
            if self._v1_task is not None and not self._v1_task.done():
                self._v1_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await self._v1_task
                self._v1_task = None
            for task in helpers:
                task.cancel()
            for task in helpers:
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def _steady_poll_interval(self) -> float:
        """HTTP 快照轮询间隔：读刷新节奏配置（vvtr 渠道），钳位 [2, 30]

        接入管理后台「刷新节奏」页的 per-channel 配置；下限 2 秒为
        HTTP 限频保护（1 rps 还需给 K线历史请求留带宽）。
        """
        try:
            from app.services.refresh_cfg import get_refresh_interval

            sec = await get_refresh_interval(self._redis, "vvtr")
        except Exception:
            sec = _HTTP_FALLBACK_INTERVAL
        try:
            sec = float(sec)
        except (TypeError, ValueError):
            sec = _HTTP_FALLBACK_INTERVAL
        return max(2.0, min(30.0, sec))

    async def _http_steady_loop(self, cfg: VvtrRuntimeConfig) -> None:
        """HTTP 快照稳态轮询：3s/轮主力合约，v1 出推送即让位

        退出条件：配置关闭、v2 定期重试到点（返回外层重编排名单；
        WSS 停用时跳过重试持续供数）、或并行 v1 会话开始实际供数。
        """
        self._mode = "http"
        ws_on = getattr(cfg, "ws_enabled", True)
        logger.info(
            "VVTR HTTP 快照稳态模式启动（%.0fs/轮，%s）",
            _HTTP_FALLBACK_INTERVAL,
            f"{_V2_RETRY_INTERVAL:.0f}s 后重试 v2 深度" if ws_on else "WSS 已停用",
        )
        last_v2_retry = asyncio.get_event_loop().time()
        while not self._stop:
            loop_now = asyncio.get_event_loop().time()
            # v1 并行会话开始实际供数 → HTTP 让位
            v1 = self._v1_task
            if v1 is not None and not v1.done() and self._subscribed_symbols > 0:
                logger.info("VVTR v1 WS 推送恢复，HTTP 快照轮询让位")
                return
            # v1 会话已结束/静默：当前由 HTTP 供数（修正心跳 mode 标签）
            if v1 is None or v1.done():
                self._mode = "http"
            # 定期重试 v2（套餐升级自动升回深度推送；WSS 停用时不重试）
            if (
                getattr(cfg, "ws_enabled", True)
                and loop_now - last_v2_retry >= _V2_RETRY_INTERVAL
            ):
                logger.info("VVTR 稳态模式定期重试 v2 深度行情")
                return
            # 配置热加载：关闭/换密钥即退出稳态；密钥/地址变更即时生效；
            # 同时刷新开关镜像（稳态长期运行，防止 market:vvtr:state TTL 过期）
            try:
                fresh = await get_vvtr_runtime_config(self._session_factory)
                if not fresh.ws_active:
                    logger.info("VVTR 配置已关闭，退出 HTTP 稳态模式")
                    self._connected = False
                    return
                cfg = fresh
                await write_vvtr_state_to_redis(
                    self._redis,
                    enabled=fresh.enabled,
                    quotes_enabled=fresh.quotes_enabled,
                    kline_enabled=fresh.kline_enabled,
                    ws_enabled=fresh.ws_enabled,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("VVTR 稳态模式配置读取失败", exc_info=True)
            n = await self._http_fallback_once(cfg)
            self._connected = n > 0
            await asyncio.sleep(await self._steady_poll_interval())

    async def _http_fallback_session(
        self, cfg: VvtrRuntimeConfig, duration: float
    ) -> None:
        """退避等待期的 HTTP 兜底（duration 秒后被外层取消）"""
        deadline = asyncio.get_event_loop().time() + duration
        self._mode = "http"
        while not self._stop and asyncio.get_event_loop().time() < deadline:
            await self._http_fallback_once(cfg)
            self._connected = True
            await asyncio.sleep(min(_HTTP_FALLBACK_INTERVAL, 1.0))

    def stop(self) -> None:
        self._stop = True


async def vvtr_ws_bridge_loop(
    redis_conn: aioredis.Redis,
    session_factory: Any,
) -> None:
    """scheduler master 入口：常驻运行 VVTR WS 桥"""
    bridge = VvtrWsBridge(redis_conn, session_factory)
    try:
        await bridge.run()
    except asyncio.CancelledError:
        bridge.stop()
        raise
    except Exception:
        # 常驻任务不应静默退出：记录后由 scheduler 容器重启策略兜底
        logger.exception("VVTR WS 桥异常退出")
        raise
