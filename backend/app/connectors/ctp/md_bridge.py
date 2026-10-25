"""CTP 行情桥 —— SimNow / openctp 独立进程

用法: python -m app.connectors.ctp.md_bridge
凭据与前置按 Redis market:channel + 环境变量解析，禁止日志打印密码。
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from typing import Any

from app.connectors.ctp.normalize import depth_to_quote
from app.data.contracts import ALL_CONTRACTS

logger = logging.getLogger("qihuo.ctp_md_bridge")

# 休市不写新价；TTL 拉长避免冻结价被过期抹掉（开盘 tick 会续期）
QUOTE_TTL = 7 * 24 * 3600

# 自愈：交易时段内 tick 停滞时，实盘(real_ctp)先在 RESUBSCRIBE_THRESHOLD 主动重订阅
# 软恢复（会话被服务端遗忘时往往秒级恢复）；仿真(simnow/openctp)会话静默死亡时重订阅
# 无效（已实测：重订阅后服务端仍不推送），直接跳过软恢复更快重启。超过 STALL_TIMEOUT
# 仍无 tick 才 os._exit(1) 重启进程，由 docker restart:unless-stopped 拉起干净新进程。
_TICK_STALL_TIMEOUT_LIVE = 30      # 交易时段正常 tick 间隙秒级，30s 判僵死（仿真跳过软恢复，最坏中断~35s）
_TICK_STALL_TIMEOUT_VIRTUAL = 150  # openctp 7×24 间隙略大
_TICK_COLD_START_TIMEOUT = 180     # 进入交易时段后等待首 tick 的宽限
_TICK_RESUBSCRIBE_THRESHOLD_LIVE = 20     # live 停滞 20s 触发主动重订阅软恢复（仅 real_ctp 启用）
_TICK_RESUBSCRIBE_THRESHOLD_VIRTUAL = 40  # openctp 7×24 停滞 40s 触发重订阅
# ctp_sim（CTP 中转模拟）：订阅即推一次性快照、无连续流，周期重订阅拉新快照，
# 保持源键 recv_ts 新鲜（merge/K线 forming 均有 90s 陈旧过滤）
_SNAPSHOT_REFRESH_SEC = 30


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


class CtpMdBridge:
    """CTP 行情连接器：登录 → 订阅 → 写 Redis（按 trading_mode 前缀）"""

    def __init__(
        self,
        broker_id: str,
        investor_id: str,
        password: str,
        md_front: str,
        redis_url: str,
        app_id: str,
        auth_code: str,
        user_product_info: str,
        channel_label: str,
        trading_mode: str = "live",
        allow_off_hours: bool = False,
    ) -> None:
        self.broker_id = broker_id
        self.investor_id = investor_id
        self.password = password
        self.md_front = md_front
        self.redis_url = redis_url
        self.app_id = app_id
        self.auth_code = auth_code
        self.user_product_info = user_product_info
        self.channel_label = channel_label
        self.trading_mode = trading_mode or "live"
        # virtual/openctp 7×24：允许非国内交易时段写价
        self.allow_off_hours = bool(allow_off_hours)
        self._redis: Any = None
        self._api: Any = None
        self._running = True
        # 自愈：tick 活跃跟踪（_last_tick_ts 仅 on_tick 更新；跳变检测见 _check_tick_health）
        self._last_tick_ts: float | None = None
        self._trading_baseline_ts: float | None = None
        self._was_trading: bool = False
        _stall_default = (
            _TICK_STALL_TIMEOUT_VIRTUAL
            if self.trading_mode == "virtual"
            else _TICK_STALL_TIMEOUT_LIVE
        )
        self._tick_stall_timeout = float(
            _env("CTP_TICK_STALL_TIMEOUT") or str(_stall_default)
        )
        self._cold_start_timeout = float(
            _env("CTP_COLD_START_TIMEOUT") or str(_TICK_COLD_START_TIMEOUT)
        )
        _resub_default = (
            _TICK_RESUBSCRIBE_THRESHOLD_VIRTUAL
            if self.trading_mode == "virtual"
            else _TICK_RESUBSCRIBE_THRESHOLD_LIVE
        )
        # 软恢复阈值需严格小于硬超时，否则退化为直接重启（留 5s 观察窗）
        self._resubscribe_threshold = min(
            float(_env("CTP_TICK_RESUBSCRIBE_THRESHOLD") or str(_resub_default)),
            self._tick_stall_timeout - 5.0,
        )
        # 软恢复仅对实盘 CTP 启用：仿真(simnow/openctp)会话静默死亡时重订阅无效
        # （服务端会话已死，重新 SubscribeMarketData 也不再推送），跳过以更快重启。
        # 可用 CTP_RESUBSCRIBE_ENABLED=1 强制开启（调试/实盘前仿真验证用）。
        self._resubscribe_enabled = str(self.channel_label).lower().startswith(
            "real"
        ) or _env("CTP_RESUBSCRIBE_ENABLED") == "1"
        # 当前停滞周期内是否已尝试过重订阅（on_tick 收到新 tick 后重置）
        self._resubscribe_attempted = False
        # ctp_sim 快照源：无连续流，跳过 tick 停滞自愈（避免交易时段连环重启），
        # 依赖 CTP 前置自动重连 + 周期重订阅刷新快照
        self._snapshot_source = str(self.channel_label).lower() == "ctp_sim"
        self._snapshot_last_refresh = time.time()
        # 快照缓存（symbol → 已写 quote）：服务端只推首订快照、重订阅不重推
        # （2026-10-11 实测），保温重写让源键 recv_ts 保持新鲜（merge 90s 门槛）
        self._snapshot_cache: dict[str, dict[str, Any]] = {}
        self._contract_map = {c["symbol"].lower(): c for c in ALL_CONTRACTS}
        # 订阅含郑商所三/四位年月与大小写别名（openctp 兼容）
        from app.connectors.ctp.instrument_map import build_subscribe_instruments

        self._symbols = build_subscribe_instruments(ALL_CONTRACTS)

    def connect_redis(self) -> None:
        import redis as sync_redis

        self._redis = sync_redis.from_url(self.redis_url, decode_responses=True)
        self._redis.ping()
        logger.info("Redis 已连接")
        self._hb_last: list[float] = []
        # 写入本 bridge 对应的 channel 镜像，便于排查
        try:
            from app.services.market_keys import channel_key

            self._redis.set(
                channel_key(self.trading_mode),
                self.channel_label or "unknown",
            )
        except Exception:
            logger.warning("写入 channel 镜像失败", exc_info=True)
        try:
            from app.connectors.ctp.bridge_heartbeat import write_bridge_heartbeat

            write_bridge_heartbeat(
                self._redis,
                self.trading_mode,
                self.channel_label or "unknown",
                0.0,
                self._hb_last,
            )
        except Exception:
            pass

    def write_quote(self, quote: dict[str, Any]) -> None:
        if self._redis is None:
            return
        symbol = quote.get("symbol")
        if not symbol:
            return
        from app.services.market_keys import source_quote_key, write_keys_for_quote
        from app.services.trading_hours import is_symbol_trading

        # live 默认休市不覆盖；virtual 7×24 允许写
        if not self.allow_off_hours and not is_symbol_trading(str(symbol)):
            return
        quote["recv_ts"] = time.time()
        if self.trading_mode != "virtual":
            source = str(self.channel_label or "ctp")
            quote["source"] = source
            keys = [source_quote_key(source, str(symbol))]
        else:
            keys = write_keys_for_quote(self.trading_mode, str(symbol))
            # virtual：标明 openctp 来源，便于排查串盘
            if not quote.get("source"):
                quote["source"] = f"ctp:{self.channel_label}"
            # 真 tick 锚价：脉冲只允许围绕此价 ±1 跳，禁止随机游走
            try:
                lp = float(quote.get("last_price") or 0)
            except (TypeError, ValueError):
                lp = 0.0
            if lp > 0:
                quote["anchor_price"] = lp
        payload = json.dumps(quote, ensure_ascii=False)
        for key in keys:
            self._redis.set(key, payload, ex=QUOTE_TTL)
        try:
            from app.connectors.ctp.bridge_heartbeat import write_bridge_heartbeat

            write_bridge_heartbeat(
                self._redis,
                self.trading_mode,
                self.channel_label or "unknown",
                5.0,
                self._hb_last,
            )
        except Exception:
            pass

    def on_tick(self, tick: dict[str, Any]) -> None:
        self._last_tick_ts = time.time()
        # 收到新 tick = 会话已恢复，清掉重订阅标记进入新一轮观察
        self._resubscribe_attempted = False
        quote = depth_to_quote(tick, self._contract_map)
        if quote is None:
            return
        if self._snapshot_source:
            self._snapshot_cache[str(quote.get("symbol") or "")] = dict(quote)
        self.write_quote(quote)

    def _snapshot_keep_warm(self) -> None:
        """ctp_sim 保温：缓存 quote 以新 recv_ts 重写源键。

        服务端订阅即推一次性快照（重订阅不重推），若不保温，
        merge/K线 forming 的 90s 陈旧过滤会在 90s 后把模拟价回落
        到休市冻结的新浪价。价格本身不变，只刷新 recv_ts。
        """
        if self._redis is None:
            return
        from app.services.market_keys import source_quote_key

        source = str(self.channel_label or "ctp")
        for symbol, quote in self._snapshot_cache.items():
            try:
                payload = dict(quote)
                payload["source"] = source
                payload["recv_ts"] = time.time()
                self._redis.set(
                    source_quote_key(source, symbol),
                    json.dumps(payload, ensure_ascii=False),
                    ex=QUOTE_TTL,
                )
            except Exception:  # noqa: BLE001 — 单合约失败不阻断
                logger.debug("ctp_sim 保温重写失败 %s", symbol, exc_info=True)

    def _resubscribe(self) -> None:
        """软恢复：重新发送订阅请求，针对「会话被服务端遗忘、连接仍在但不推 tick」。

        CTP 协议允许重复订阅（服务端去重），无需先退订；重订阅后若软恢复成功，
        on_tick 会刷新 _last_tick_ts 并清掉 _resubscribe_attempted。
        """
        api = self._api
        if api is None:
            return
        instruments = [s.encode("utf-8") for s in self._symbols]
        batch = 50
        try:
            for i in range(0, len(instruments), batch):
                chunk = instruments[i : i + batch]
                api.SubscribeMarketData(chunk, len(chunk))
                time.sleep(0.02)
            logger.info("CTP 已重新订阅 %d 合约尝试软恢复", len(instruments))
        except Exception:
            logger.error("CTP 重新订阅异常", exc_info=True)

    def _check_tick_health(self) -> None:
        """交易时段内持续无 tick 则自愈重启，覆盖「重连后僵死」与「冷启动连不上」。

        virtual(openctp 7×24) 始终判定；live 仅 is_trading_hours() 为真时判定，
        避免休市/午休合法无 tick 被误杀。检测到「从非交易跨入交易」时重置 tick
        基准，避免跨时段（夜盘→日盘）elapsed 累积导致的开盘瞬间误判。

        ctp_sim 快照源跳过判定：订阅即推一次性快照属正常形态，
        停滞自愈会在交易时段造成 ~35s 一次的连环重启。
        """
        if self._snapshot_source:
            return
        if self.trading_mode == "virtual":
            trading = True
        else:
            from app.services.trading_hours import is_trading_hours

            trading = is_trading_hours()

        now = time.time()
        if not trading:
            self._was_trading = False
            return

        # 进入新交易时段（含冷启动）：重置 tick 基准，重新等首 tick
        if not self._was_trading:
            self._last_tick_ts = None
            self._trading_baseline_ts = now
            self._was_trading = True

        if self._last_tick_ts is not None:
            elapsed = now - self._last_tick_ts
            # 软恢复：停滞达到阈值且本轮尚未重订阅 → 主动重新订阅，优先秒级恢复。
            # 仅 real_ctp 启用（仿真重订阅无效，跳过以更快重启）。
            if (
                self._resubscribe_enabled
                and elapsed > self._resubscribe_threshold
                and not self._resubscribe_attempted
            ):
                logger.warning(
                    "CTP 行情 tick 停滞 %.0f 秒，主动重订阅尝试软恢复 "
                    "mode=%s channel=%s",
                    elapsed,
                    self.trading_mode,
                    self.channel_label,
                )
                self._resubscribe()
                self._resubscribe_attempted = True
            if elapsed > self._tick_stall_timeout:
                reason = (
                    "tick_stall_after_resubscribe"
                    if self._resubscribe_attempted
                    else "tick_stall"
                )
                logger.error(
                    "CTP 行情 tick 停滞 %.0f 秒（阈值 %.0f），重订阅未恢复，自愈重启 "
                    "mode=%s channel=%s",
                    elapsed,
                    self._tick_stall_timeout,
                    self.trading_mode,
                    self.channel_label,
                )
                self._persist_last_exit(reason, elapsed)
                os._exit(1)
        else:
            if self._trading_baseline_ts is None:
                self._trading_baseline_ts = now
            elapsed = now - self._trading_baseline_ts
            if elapsed > self._cold_start_timeout:
                logger.error(
                    "CTP 行情进场 %.0f 秒仍无首 tick（阈值 %.0f），自愈重启 "
                    "mode=%s channel=%s",
                    elapsed,
                    self._cold_start_timeout,
                    self.trading_mode,
                    self.channel_label,
                )
                self._persist_last_exit("cold_start_no_tick", elapsed)
                os._exit(1)

    def _persist_last_exit(self, reason: str, elapsed: float) -> None:
        """os._exit 前把退出原因写 Redis，供 backend 代发自愈告警。

        bridge 是独立进程、无 DB/async，无法直接发 ops_alert；
        写 market:{mode}:bridge:last_exit，backend 的 channel_health_monitor_loop
        检测 ts 变化后代发 push_ops_alert(bridge_self_heal)。
        """
        if self._redis is None:
            return
        try:
            self._redis.set(
                f"market:{self.trading_mode}:bridge:last_exit",
                json.dumps(
                    {
                        "ts": time.time(),
                        "reason": reason,
                        "elapsed": round(elapsed, 1),
                        "channel": self.channel_label,
                        "mode": self.trading_mode,
                    },
                    ensure_ascii=False,
                ),
                ex=7 * 24 * 3600,
            )
        except Exception:
            pass

    def run(self) -> int:
        if not self.password:
            logger.error("CTP 密码未设置（REAL_CTP_PASSWORD / OPENCTP_PASSWORD）")
            return 2
        if not self.md_front:
            logger.error("CTP 行情前置未设置")
            return 2

        try:
            self.connect_redis()
        except Exception:
            logger.error("Redis 连接失败", exc_info=True)
            return 3

        try:
            self._run_openctp()
            return 0
        except ImportError:
            logger.error(
                "未安装 CTP 绑定。bridge 镜像需 pip install openctp-ctp"
            )
            return 4
        except Exception:
            logger.error("CTP 行情桥异常退出", exc_info=True)
            return 1

    def _load_mdapi(self) -> Any:
        """按渠道选择 md 库

        openctp 模拟环境必须用 TTS 兼容库（openctp_tts），官方 CTP 库会 4097 断连。
        SimNow 用官方/openctp_ctp 库。
        """
        errors: list[str] = []
        ch = str(self.channel_label or "")
        if ch.startswith("openctp"):
            candidates = (
                "openctp_tts.thostmduserapi",
                "openctp_tts.mdapi",
                "openctp_tts",
                "openctp_ctp.thostmduserapi",
            )
        else:
            candidates = (
                "openctp_ctp.thostmduserapi",
                "openctp_ctp.mdapi",
                "openctp_ctp",
                "openctp_tts.thostmduserapi",
            )
        for name in candidates:
            try:
                mod = __import__(name, fromlist=["*"])
                if hasattr(mod, "CThostFtdcMdApi"):
                    logger.info("已加载 CTP md 模块: %s", name)
                    return mod
                inner = getattr(mod, "mdapi", None) or getattr(
                    mod, "thostmduserapi", None
                )
                if inner is not None and hasattr(inner, "CThostFtdcMdApi"):
                    logger.info("已加载 CTP md 模块: %s.%s", name, "inner")
                    return inner
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
        raise ImportError(
            "无法加载 CTP/TTS mdapi（openctp 请 pip install openctp-tts）: "
            + "; ".join(errors)
        )

    def _run_openctp(self) -> None:
        mdapi = self._load_mdapi()
        bridge = self

        class _Spi(mdapi.CThostFtdcMdSpi):  # type: ignore[misc]
            def OnFrontConnected(self) -> None:  # noqa: N802
                logger.info("CTP 前置已连接，发起登录")
                req = mdapi.CThostFtdcReqUserLoginField()
                req.BrokerID = bridge.broker_id
                req.UserID = bridge.investor_id
                req.Password = bridge.password
                if bridge.user_product_info:
                    req.UserProductInfo = bridge.user_product_info[:10]
                self_api = bridge._api
                if self_api is not None:
                    self_api.ReqUserLogin(req, 0)

            def OnRspError(self, pRspInfo, nRequestID, bIsLast):  # noqa: N802
                if pRspInfo:
                    logger.error(
                        "CTP OnRspError ErrorID=%s",
                        getattr(pRspInfo, "ErrorID", "?"),
                    )

            def OnRspUserLogin(self, pRspUserLogin, pRspInfo, nRequestID, bIsLast):  # noqa: N802
                err = 0
                if pRspInfo:
                    err = int(getattr(pRspInfo, "ErrorID", 0) or 0)
                if err != 0:
                    logger.error("CTP 登录失败 ErrorID=%s", err)
                    return
                logger.info("CTP 登录成功，订阅 %d 合约", len(bridge._symbols))
                batch = 50
                instruments = [s.encode("utf-8") for s in bridge._symbols]
                api = bridge._api
                if api is None:
                    return
                for i in range(0, len(instruments), batch):
                    chunk = instruments[i : i + batch]
                    api.SubscribeMarketData(chunk, len(chunk))
                    time.sleep(0.05)
                logger.info("订阅请求已发送")

            def OnRtnDepthMarketData(self, pDepthMarketData) -> None:  # noqa: N802
                if pDepthMarketData is None:
                    return
                d = pDepthMarketData
                tick = {
                    "InstrumentID": getattr(d, "InstrumentID", ""),
                    "LastPrice": getattr(d, "LastPrice", 0),
                    "PreSettlementPrice": getattr(d, "PreSettlementPrice", 0),
                    "PreClosePrice": getattr(d, "PreClosePrice", 0),
                    "PreOpenInterest": getattr(d, "PreOpenInterest", 0),
                    "OpenPrice": getattr(d, "OpenPrice", 0),
                    "HighestPrice": getattr(d, "HighestPrice", 0),
                    "LowestPrice": getattr(d, "LowestPrice", 0),
                    "BidPrice1": getattr(d, "BidPrice1", 0),
                    "AskPrice1": getattr(d, "AskPrice1", 0),
                    "BidVolume1": getattr(d, "BidVolume1", 0),
                    "AskVolume1": getattr(d, "AskVolume1", 0),
                    "BidPrice2": getattr(d, "BidPrice2", 0),
                    "AskPrice2": getattr(d, "AskPrice2", 0),
                    "BidVolume2": getattr(d, "BidVolume2", 0),
                    "AskVolume2": getattr(d, "AskVolume2", 0),
                    "BidPrice3": getattr(d, "BidPrice3", 0),
                    "AskPrice3": getattr(d, "AskPrice3", 0),
                    "BidVolume3": getattr(d, "BidVolume3", 0),
                    "AskVolume3": getattr(d, "AskVolume3", 0),
                    "BidPrice4": getattr(d, "BidPrice4", 0),
                    "AskPrice4": getattr(d, "AskPrice4", 0),
                    "BidVolume4": getattr(d, "BidVolume4", 0),
                    "AskVolume4": getattr(d, "AskVolume4", 0),
                    "BidPrice5": getattr(d, "BidPrice5", 0),
                    "AskPrice5": getattr(d, "AskPrice5", 0),
                    "BidVolume5": getattr(d, "BidVolume5", 0),
                    "AskVolume5": getattr(d, "AskVolume5", 0),
                    "Volume": getattr(d, "Volume", 0),
                    "Turnover": getattr(d, "Turnover", 0),
                    "OpenInterest": getattr(d, "OpenInterest", 0),
                    "ClosePrice": getattr(d, "ClosePrice", 0),
                    "SettlementPrice": getattr(d, "SettlementPrice", 0),
                    "UpperLimitPrice": getattr(d, "UpperLimitPrice", 0),
                    "LowerLimitPrice": getattr(d, "LowerLimitPrice", 0),
                    "AveragePrice": getattr(d, "AveragePrice", 0),
                    "UpdateTime": getattr(d, "UpdateTime", ""),
                    "UpdateMillisec": getattr(d, "UpdateMillisec", 0),
                    "ActionDay": getattr(d, "ActionDay", ""),
                }
                bridge.on_tick(tick)

            def OnFrontDisconnected(self, nReason: int) -> None:  # noqa: N802
                logger.warning("CTP 前置断开 reason=%s", nReason)

        flow = _env("SIMNOW_FLOW_PATH", "/tmp/qihuo_ctp_md_flow")
        os.makedirs(flow, exist_ok=True)
        api = mdapi.CThostFtdcMdApi.CreateFtdcMdApi(flow + "/")
        self._api = api
        spi = _Spi()
        api.RegisterSpi(spi)
        api.RegisterFront(self.md_front)
        api.Init()
        logger.info(
            "CTP MdApi Init channel=%s broker=%s investor=%s front=%s",
            self.channel_label,
            self.broker_id,
            self.investor_id,
            self.md_front,
        )

        def _stop(*_args: Any) -> None:
            self._running = False

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)
        # 主循环独立写心跳：午休/无成交时段 CTP 不推 tick 时仍保持 green，
        # 避免健康监控误判 red 反复重启 bridge
        while self._running:
            time.sleep(0.5)
            try:
                from app.connectors.ctp.bridge_heartbeat import write_bridge_heartbeat

                write_bridge_heartbeat(
                    self._redis,
                    self.trading_mode,
                    self.channel_label or "unknown",
                    5.0,
                    self._hb_last,
                )
            except Exception:
                pass
            # ctp_sim 快照源：周期重订阅拉新快照（若服务端支持）+ 保温重写
            # （重订阅不重推时维持源键新鲜度）
            if (
                self._snapshot_source
                and time.time() - self._snapshot_last_refresh >= _SNAPSHOT_REFRESH_SEC
            ):
                self._snapshot_last_refresh = time.time()
                self._resubscribe()
                self._snapshot_keep_warm()
            self._check_tick_health()
        try:
            api.Release()
        except Exception:
            pass


def _read_channel(redis_url: str, trading_mode: str) -> str:
    from app.services.market_channel import (
        CHANNEL_OPENCTP_724,
        is_ctp_channel,
        normalize_channel,
    )
    from app.services.market_keys import LEGACY_CHANNEL_KEY, channel_key

    if trading_mode == "virtual":
        return CHANNEL_OPENCTP_724

    channel = CHANNEL_OPENCTP_724
    try:
        import redis as sync_redis

        client = sync_redis.from_url(redis_url, decode_responses=True)
        raw = client.get(channel_key("live")) or client.get(LEGACY_CHANNEL_KEY)
        client.close()
        if raw:
            channel = normalize_channel(str(raw))
    except Exception:
        logger.warning("读取 market channel 失败，默认 openctp_724", exc_info=True)

    if not is_ctp_channel(channel):
        # live bridge 在管理端仍是新浪时，可挂 simnow/openctp 待命
        channel = CHANNEL_OPENCTP_724
    return channel


def _read_ctp_sim_config(redis_url: str) -> dict[str, str]:
    """读管理端写入的 ctp_sim 渠道配置（JSON），失败返回空 dict。

    管理后台保存（system_settings）后镜像到 Redis，bridge 重启时据此
    覆盖 env 默认前置/账号；密码仅存内网 Redis，不进日志。
    """
    try:
        import json as _json

        import redis as sync_redis

        from app.services.market_channel import CTP_SIM_CONFIG_KEY

        client = sync_redis.from_url(redis_url, decode_responses=True)
        raw = client.get(CTP_SIM_CONFIG_KEY)
        client.close()
        if not raw:
            return {}
        data = _json.loads(raw)
        if not isinstance(data, dict):
            return {}
        return {
            k: str(v)
            for k, v in data.items()
            if k in ("md_front", "broker_id", "investor_id", "password")
            and str(v or "").strip()
        }
    except Exception:
        logger.warning("读取 ctp_sim 配置失败，回退 env 默认", exc_info=True)
        return {}


def build_bridge_from_env() -> CtpMdBridge:
    """从环境变量 + Redis 渠道构建 bridge

    CTP_BRIDGE_MODE=live|virtual 决定写哪套 Redis 命名空间。
    virtual 默认 openctp_724（可 CTP_BRIDGE_VIRTUAL_CHANNEL 换源，
    如 ctp_sim 用 CTP 中转模拟跑 7×24 虚拟盘），并允许非交易时段写价。
    """
    from app.services.market_channel import (
        CHANNEL_CTP_SIM,
        resolve_ctp_login,
    )

    trading_mode = (_env("CTP_BRIDGE_MODE") or _env("TRADING_MODE") or "live").lower()
    if trading_mode not in ("live", "virtual"):
        trading_mode = "live"

    redis_host = _env("REDIS_HOST", "127.0.0.1")
    redis_port = _env("REDIS_PORT", "6379")
    redis_db = _env("REDIS_DB", "0")
    redis_url = _env("REDIS_URL") or f"redis://{redis_host}:{redis_port}/{redis_db}"

    # 可强制渠道，避免读 Redis 被 live 管理端覆盖
    forced_channel = _env("CTP_BRIDGE_CHANNEL")
    if forced_channel:
        channel = forced_channel.strip().lower()
    else:
        channel = _read_channel(redis_url, trading_mode)

    if trading_mode == "virtual":
        from app.services.market_channel import is_ctp_channel, normalize_channel

        virtual_channel = normalize_channel(
            _env("CTP_BRIDGE_VIRTUAL_CHANNEL") or "openctp_724"
        )
        # 非法值/非 CTP 渠道回落 openctp_724（virtual 必须是 CTP 类渠道）
        if not is_ctp_channel(virtual_channel):
            virtual_channel = "openctp_724"
        channel = virtual_channel

    login = resolve_ctp_login(channel)
    # 管理端保存的 ctp_sim 配置（Redis 镜像）优先于 env 默认
    if login["channel"] == CHANNEL_CTP_SIM:
        cfg = _read_ctp_sim_config(redis_url)
        for key in ("md_front", "broker_id", "investor_id", "password"):
            if cfg.get(key):
                login[key] = cfg[key]
    force = _env("SIMNOW_MD_FRONT_FORCE") or _env("CTP_MD_FRONT_FORCE")
    md_front = force or login["md_front"]
    allow_off = (
        trading_mode == "virtual"
        or login["channel"] == CHANNEL_CTP_SIM
        or _env("CTP_ALLOW_OFF_HOURS") in ("1", "true", "yes")
    )
    logger.info(
        "bridge mode=%s channel=%s front=%s investor=%s off_hours=%s",
        trading_mode,
        login["channel"],
        md_front,
        login["investor_id"],
        allow_off,
    )
    return CtpMdBridge(
        broker_id=login["broker_id"],
        investor_id=login["investor_id"],
        password=login["password"],
        md_front=md_front,
        redis_url=redis_url,
        app_id=login["app_id"],
        auth_code=login["auth_code"],
        user_product_info=login["user_product_info"],
        channel_label=login["channel"],
        trading_mode=trading_mode,
        allow_off_hours=allow_off,
    )


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    bridge = build_bridge_from_env()
    sys.exit(bridge.run())


if __name__ == "__main__":
    main()
