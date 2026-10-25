"""VVTR 分发容器（relay）—— 单 apiKey 多服务器共享的行情中继

背景：VVTR 套餐（一个 apiKey）需要 33（测试）/58（生产）共用，而上游
硬约束是每 key 仅 1 条 WSS 连接 + HTTP 1 req/s（key 级限频）。relay
持有唯一上游连接与统一 HTTP 预算，下游服务器把 vvtr_base_url /
vvtr_ws_url 指到 relay 即可，行为与直连官方一致（计划见
docs/PLAN-vvtr-relay.md）。

职责：
- 上游 WSS：v2(403)→v1 降级链（10 分钟重试 v2）、45s ping、1s→60s
  退避重连；订阅清单 = 烘焙主力基线 + 附加观察合约（合约表同品种
  第二行，如 M 的 m2705）∩ /symbols 有效清单（cap 100）；
  每 12h 用 realSymbol(9999) + later_symbol（月份只进不退）刷新主力，
  清单变化即重连（上游订阅是连接时指定的覆盖式）；
- 防抢连接预检：连上游前轮询对端心跳键 market:live:vvtr:heartbeat，
  桥仍占用上游（mode 非 idle）则拒绝连接，从机制上保证「每 key 仅
  1 连接」；RELAY_WS_VERSION=1 可固定 v1（套餐暂不含 v2 时不探测）；
- 下游 WSS /v1/connect：welcome 帧 + 上游帧广播（v1 上游翻成 v2 帧
  形——下游桥对配置 ws_url 一律按 v2 会话解析；v2 上游原样透传；
  剥层仅用于统计），不限连接数；
- HTTP 代理（同端口）：下游 apiKey 一律丢弃、统一注入容器 env 真钥，
  转发前过全局 1 rps 节流（复用 http_client._throttle），响应按路由
  TTL 缓存（键 = 路径 + 去 apiKey 的排序参数，上限 1 万条 LRU）；
  realSymbol 半静态走 6h 长缓存并与内部主力刷新共键共享（2026-09-02
  前 30s + 内部扫表直连上游，12h 同步批量撞 1rps 预算引发 429 风暴），
  下游带 fresh=1 可穿透重取；
- 运维端点：/health、/stats。

Phase 1 边界：不落库、不动 33 现有同步任务、不新增镜像/依赖。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect

from app.connectors.vvtr.config import (
    DEFAULT_BASE_URL,
    DEFAULT_WS_URL,
    cap_symbol_list,
    match_valid_vvtr_symbols,
)
from app.connectors.vvtr.http_client import _mask, _throttle
from app.connectors.vvtr.quote_map import parse_vvtr_frames
from app.services.contracts_sync import later_symbol

logger = logging.getLogger("qihuo.relay")

# 对端（33 桥）心跳键：存在 = 旧源还在跑，relay 不得连上游
PEER_HEARTBEAT_KEY = "market:live:vvtr:heartbeat"

# 单连接订阅上限（官方）；主力刷新 / 有效清单缓存周期
SUBSCRIBE_CAP = 100
MASTER_REFRESH_INTERVAL = 12 * 3600.0
VALID_SYMBOLS_TTL = 600.0
V2_RETRY_INTERVAL = 600.0

# HTTP 代理路由 → 响应缓存 TTL（秒）
_PROXY_TTLS: dict[str, float] = {
    "snapshot": 1.0,
    "kline/current": 1.0,
    "symbols": 30.0,
    # realSymbol 半静态（主力以天/周变化）：6h 长缓存并与内部主力刷新
    # 共键共享（2026-09-02 修复：12h 同步批量 realSymbol 撞 1rps 预算
    # 引发 429 风暴，VVTR key 有超频黑名单风险）。人工核对可带 fresh=1
    # 穿透重取。
    "realSymbol": 6 * 3600.0,
    "kline/history": 60.0,
    "kline/historyn": 60.0,
}

# 内部主力刷新扫表：每次真实出网后的间隔（与 K 线流量共享 1rps 预算，
# 持续贴满会触发 VVTR key 级限频；缓存命中不等待）
_REAL_SYMBOL_SWEEP_GAP = 2.0

_CACHE_MAXSIZE = 10000
_QUEUE_SIZE = 2000  # 每下游发送队列上限（慢消费者丢最旧，不背压）


# ---------- 环境配置 ----------


@dataclass
class RelaySettings:
    """relay.env（服务器侧，不进 git）；字段对应计划 4.4"""

    api_key: str = ""
    upstream_ws: str = DEFAULT_WS_URL
    upstream_rest: str = DEFAULT_BASE_URL
    listen_port: int = 8100
    token: str = ""  # Phase 1 内网信任：下游 apiKey 传它或任意值均可
    peer_redis_url: str = ""
    skip_peer_check: bool = False
    ws_version: str = "auto"  # auto=先试 v2（403 降级 v1）；"1"=固定 v1 不探测

    @classmethod
    def from_env(cls) -> "RelaySettings":
        return cls(
            api_key=os.getenv("VVTR_API_KEY", "").strip(),
            upstream_ws=os.getenv("VVTR_UPSTREAM_WS", "").strip() or DEFAULT_WS_URL,
            upstream_rest=os.getenv("VVTR_UPSTREAM_REST", "").strip() or DEFAULT_BASE_URL,
            listen_port=int(os.getenv("RELAY_LISTEN_PORT", "8100") or 8100),
            token=os.getenv("RELAY_TOKEN", "").strip(),
            peer_redis_url=os.getenv("PEER_HEARTBEAT_REDIS_URL", "").strip(),
            skip_peer_check=os.getenv("RELAY_SKIP_PEER_CHECK", "").strip()
            in ("1", "true", "True", "yes"),
            ws_version=os.getenv("RELAY_WS_VERSION", "").strip() or "auto",
        )


# ---------- 纯函数（单测覆盖） ----------


def strip_api_key(params: dict[str, Any]) -> dict[str, str]:
    """剔除下游传入的 apiKey（任意大小写变体）——真钥只在容器 env，绝不经下游之手"""
    return {
        str(k): str(v)
        for k, v in (params or {}).items()
        if str(k).strip().lower() != "apikey"
    }


def cache_key(path: str, params: dict[str, Any]) -> str:
    """缓存键 = 路径 + 排序后的无钥参数（不同下游 key 共享同一份缓存）"""
    flat = ";".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
    return f"{path}|{flat}"


def _cache_key_params(path: str, params: dict[str, Any]) -> dict[str, Any]:
    """kline 历史路由的秒级时间参数在「缓存键」上归整到分钟。

    下游当日段 to=now 的秒位每次请求都变，键永不命中 → 同一分钟内
    反复切换周期也全额重拉（每个上游请求 1.1s 预算）。归整后同分钟
    共享缓存；转发上游的参数保持原样不变。"""
    if not path.startswith("kline/history"):
        return params
    out = {}
    for k, v in params.items():
        v = str(v)
        if (
            len(v) == 19
            and v[4] == "-"
            and v[10] == " "
            and v[17:19].isdigit()
        ):
            v = v[:17] + "00"
        out[k] = v
    return out


def build_welcome_frame(now: float | None = None) -> dict[str, Any]:
    """下游连接成功即发（与官方 welcome 控制帧同构）"""
    return {
        "type": "welcome",
        "data": "连接成功 (relay)",
        "time": int(now if now is not None else time.time()),
    }


def authorized(settings: RelaySettings, apikey: str) -> bool:
    """下游凭证校验：RELAY_TOKEN 配置后必须携带 token 或真钥。

    未配置 token 时保持 Phase 1 内网信任（放行），便于灰度迁移：
    服务器先配好下游凭证，再在 relay.env 启用 RELAY_TOKEN。
    """
    if not settings.token:
        return True
    key = str(apikey or "").strip()
    return bool(key) and key in (settings.token, settings.api_key)


def initial_masters() -> dict[str, str]:
    """订阅基线：烘焙合约表（2026-08-20 迁移基线）品种 → 主力"""
    from app.data.contracts import ALL_CONTRACTS

    out: dict[str, str] = {}
    for c in ALL_CONTRACTS:
        code = str(c.get("code") or "").strip().upper()
        sym = str(c.get("symbol") or "").strip().lower()
        if code and sym and code not in out:
            out[code] = sym
    return out


def extra_watch_symbols() -> list[str]:
    """附加观察合约：合约表中与主力行不同月的行（如 M 的 m2705）

    上游订阅是连接时覆盖式指定，且下游 symbols 参数不参与上游订阅
    （relay 广播给全部下游）——需要推送的附加合约必须并入上游清单。
    不占 initial_masters 的品种键位（12h 主力刷新按品种只进不退，
    附加行混入会挡回退/误前进）；下市后由 /symbols 有效清单自然剔除。
    """
    from app.data.contracts import ALL_CONTRACTS

    masters = initial_masters()
    seen = {s for s in masters.values() if s}
    out: list[str] = []
    for c in ALL_CONTRACTS:
        sym = str(c.get("symbol") or "").strip().lower()
        if sym and sym not in seen:
            seen.add(sym)
            out.append(sym)
    return out


def apply_master_refresh(
    current: dict[str, str], fetched: dict[str, str]
) -> dict[str, str]:
    """realSymbol(9999) 刷新结果合入现表：later_symbol 月份只进不退

    VVTR 主力尚未跟上时保持现值，真正换月后自然对齐（contracts_sync
    同款语义）。【2026-09-02 事故教训：曾改双向跟随，实测 VVTR 9999
    整体滞后平台基线一代（返回 rb2610 等旧主力），双向把订阅清单倒退
    一代——必须只进不退】。新品种直接补入。返回新 dict，不修改入参。
    """
    out = dict(current)
    for code, incoming in (fetched or {}).items():
        merged = later_symbol(out.get(code, ""), str(incoming or ""))
        if merged:
            out[code] = merged
    return out


def translate_v1_frame(msg: dict[str, Any]) -> dict[str, Any]:
    """v1 tick 帧 → v2 深度帧字段形（下游桥才可用 v2 映射器全字段解析）

    实测发现：桥把配置的 ws_url 一律先按 v2 会话尝试（无 403 即用
    v2 映射器），relay 上游为 v1 时若原样透传，下游只能拿到 last_price
    （o/h/l/cV/cP/cA/quotes 均不识别）。两版字段同构、仅命名不同：
    o/h/l→open/high/low、cV/cP/cA→total_vol/open_int/turnover、
    盘口数组 quotes→q（元素 bP/aP/bV/aV 同名）；t 由 19 位时间字符串
    补毫秒为 v2 的 17 位数字。无 s 的控制帧原样返回。
    """
    out = dict(msg)
    if not str(out.get("s") or "").strip():
        return out
    for src, dst in (
        ("o", "open"),
        ("h", "high"),
        ("l", "low"),
        ("cV", "total_vol"),
        ("cP", "open_int"),
        ("cA", "turnover"),
        ("quotes", "q"),
    ):
        if src in out and dst not in out:
            out[dst] = out.pop(src)
    t = str(out.get("t") or "")
    if len(t) == 19 and t[4] == "-" and t[7] == "-" and t[10] == " ":
        digits = t[0:4] + t[5:7] + t[8:10] + t[11:13] + t[14:16] + t[17:19] + "000"
        if len(digits) == 17 and digits.isdigit():
            out["t"] = digits
    return out


def build_subscription(
    symbols: list[str], valid: list[str], cap: int = SUBSCRIBE_CAP
) -> list[str]:
    """订阅清单：主力列表 ∩ VVTR 有效清单（防无效合约毒化订阅），cap 100

    有效清单为空（拉取失败）时回退未过滤列表；输出小写去重。
    """
    up = [str(s or "").strip().upper() for s in symbols or [] if str(s or "").strip()]
    if valid:
        matched = match_valid_vvtr_symbols(up, valid)
        if matched:
            return cap_symbol_list(matched, cap)
        logger.warning("有效清单交集为空（主力 %d 个），回退未过滤列表", len(up))
    return cap_symbol_list(up, cap)


# ---------- TTL 缓存 / 速率与统计 ----------


@dataclass
class CachedResponse:
    status: int
    content_type: str
    body: bytes


class TTLCache:
    """简易 LRU + TTL：容量上限淘汰最旧，读取时惰性过期"""

    def __init__(
        self, maxsize: int = _CACHE_MAXSIZE, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._maxsize = maxsize
        self._clock = clock
        self._data: OrderedDict[str, tuple[float, CachedResponse]] = OrderedDict()

    def get(self, key: str) -> CachedResponse | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        expires, value = entry
        if self._clock() >= expires:
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return value

    def put(self, key: str, value: CachedResponse, ttl: float) -> None:
        self._data[key] = (self._clock() + ttl, value)
        self._data.move_to_end(key)
        while len(self._data) > self._maxsize:
            self._data.popitem(last=False)

    def __len__(self) -> int:
        return len(self._data)


class RateTracker:
    """最近窗口内的上游 HTTP 请求速率（诊断用，含代理/清单/主力刷新）"""

    def __init__(self, window: float = 60.0) -> None:
        self._window = window
        self._ts: deque[float] = deque()

    def note(self) -> None:
        now = time.monotonic()
        self._ts.append(now)
        self._trim(now)

    def _trim(self, now: float) -> None:
        while self._ts and now - self._ts[0] > self._window:
            self._ts.popleft()

    def rps(self) -> float:
        if not self._ts:
            return 0.0
        now = time.monotonic()
        self._trim(now)
        return round(len(self._ts) / self._window, 3)


@dataclass
class RelayStats:
    frames_upstream: int = 0
    frames_broadcast: int = 0
    http_forwarded: int = 0
    cache_hits: int = 0
    upstream_429: int = 0
    singleflight_hits: int = 0
    pacing_rejects: int = 0

    def snapshot(self) -> dict[str, int]:
        return {
            "frames_upstream": self.frames_upstream,
            "frames_broadcast": self.frames_broadcast,
            "http_forwarded": self.http_forwarded,
            "cache_hits": self.cache_hits,
            "upstream_429": self.upstream_429,
            "singleflight_hits": self.singleflight_hits,
            "pacing_rejects": self.pacing_rejects,
        }


@dataclass
class PacingWindow:
    """429 防护窗口：60s 滑窗内 ≥threshold 次 429 → 拒绝窗口内直接 503。

    vvtr 文档明确「多次超频 apiKey 会进临时黑名单」——relay 单日 466 次
    429（2026-08-25 压测基线）是拿 key 冒险；超频风暴期间主动停手。
    """

    window: float = 60.0
    threshold: int = 3
    reject_for: float = 5.0
    _recent: deque = field(default_factory=deque)
    _until: float = 0.0

    def note_429(self) -> None:
        now = time.monotonic()
        self._recent.append(now)
        while self._recent and now - self._recent[0] > self.window:
            self._recent.popleft()
        if len(self._recent) >= self.threshold:
            self._until = now + self.reject_for
            self._recent.clear()

    def active(self) -> bool:
        return time.monotonic() < self._until

    def clear(self) -> None:
        self._recent.clear()
        self._until = 0.0


# ---------- 防抢连接预检 ----------


class PeerGate:
    """切换铁律的机制化：对端桥仍在占用上游前，拒绝连上游

    心跳键内容为占用依据：mode 非 idle（或无法解析）→ 每 interval 秒
    重查并告警；键消失或桥停源休眠（mode=idle 且未连接）→ 放行。对端
    Redis 连不上时保守视为「在跑」（无法确认就不抢连接）。58 部署设
    RELAY_SKIP_PEER_CHECK=1 跳过。
    """

    def __init__(
        self,
        redis_url: str,
        skip: bool = False,
        client: Any = None,
        interval: float = 10.0,
    ) -> None:
        self._redis_url = (redis_url or "").strip()
        self._skip = bool(skip)
        self._client = client
        self._interval = interval

    async def wait_until_clear(self) -> None:
        if self._skip:
            logger.info("RELAY_SKIP_PEER_CHECK=1：跳过对端心跳预检")
            return
        if self._client is None and not self._redis_url:
            logger.warning(
                "未配置 PEER_HEARTBEAT_REDIS_URL：无法检查 33 桥心跳，放行连接"
                "（请人工确认已按 Runbook 停旧源）"
            )
            return
        while True:
            if not await self._heartbeat_alive():
                logger.info("对端心跳键已消失，预检通过，放行连接上游")
                return
            logger.warning(
                "对端桥心跳 %s 显示仍在占用上游（非 idle），拒绝连接；%.0fs 后复查",
                PEER_HEARTBEAT_KEY,
                self._interval,
            )
            await asyncio.sleep(self._interval)

    async def _heartbeat_alive(self) -> bool:
        client = self._client if self._client is not None else await self._lazy_client()
        if client is None:
            return True
        try:
            return self._peer_active(await client.get(PEER_HEARTBEAT_KEY))
        except Exception:
            logger.warning("对端心跳检查异常，保守视为存在", exc_info=True)
            return True

    @staticmethod
    def _peer_active(raw: Any) -> bool:
        """心跳键内容判定对端是否在实际占用上游连接/预算

        实测：桥停源（vvtr_enabled=false）后进入 idle 休眠，但心跳循环
        仍在持续写键（mode=idle, connected=false）——「等键 TTL 过期」
        不会发生。仅当 mode=idle 且未连接时视为不占用；无法解析/缺字段
        一律保守视为在跑。
        """
        if raw is None:
            return False
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        try:
            data = json.loads(raw)
        except Exception:
            return True
        if not isinstance(data, dict):
            return True
        if str(data.get("mode") or "").strip() == "idle" and data.get("connected") is False:
            return False
        return True

    async def _lazy_client(self) -> Any:
        if not self._redis_url:
            return None
        if not hasattr(self, "_lazy"):
            self._lazy = aioredis.from_url(
                self._redis_url, socket_timeout=2, socket_connect_timeout=2
            )
        return self._lazy


# ---------- 下游分发 ----------


@dataclass(eq=False)  # 身份哈希：hub 用 set 簿记连接
class DownstreamClient:
    queue: asyncio.Queue


class DownstreamHub:
    """下游连接簿记 + 上游帧广播（慢消费者丢最旧，不做背压）"""

    def __init__(self, queue_size: int = _QUEUE_SIZE) -> None:
        self._clients: set[DownstreamClient] = set()
        self._queue_size = queue_size

    def register(self) -> DownstreamClient:
        client = DownstreamClient(asyncio.Queue(maxsize=self._queue_size))
        self._clients.add(client)
        return client

    def unregister(self, client: DownstreamClient) -> None:
        self._clients.discard(client)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    @staticmethod
    def _offer(queue: asyncio.Queue, text: str) -> bool:
        try:
            queue.put_nowait(text)
            return True
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()
            try:
                queue.put_nowait(text)
                return True
            except asyncio.QueueFull:
                return False

    def broadcast(self, text: str) -> int:
        """帧 → 所有下游队列；返回送达数"""
        sent = 0
        for client in list(self._clients):
            if self._offer(client.queue, text):
                sent += 1
        return sent


async def _client_sender(websocket: WebSocket, client: DownstreamClient) -> None:
    try:
        while True:
            text = await client.queue.get()
            await websocket.send_text(text)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.debug("下游发送失败，连接交由接收端回收", exc_info=True)


# ---------- 上游管理（唯一 WSS 连接） ----------


class UpstreamManager:
    """唯一上游消费者：降级链 + 退避重连 + 订阅清单维护"""

    def __init__(
        self,
        settings: RelaySettings,
        hub: DownstreamHub,
        stats: RelayStats,
        rate: RateTracker,
        gate: PeerGate | None = None,
        cache: TTLCache | None = None,
        pacing: PacingWindow | None = None,
    ) -> None:
        self._settings = settings
        self._hub = hub
        self._stats = stats
        self._rate = rate
        self._gate = gate or PeerGate(settings.peer_redis_url, settings.skip_peer_check)
        # realSymbol 与 HTTP 代理共享缓存/防护窗（RelayContext 注入）：
        # 内部刷新与下游同步共键，出网预算只花一次
        self._cache = cache if cache is not None else TTLCache()
        self._pacing = pacing if pacing is not None else PacingWindow()
        self._masters = initial_masters()
        self._extras = extra_watch_symbols()
        self._resubscribe = asyncio.Event()
        self._v2_cooldown_until = 0.0
        self._valid_cache: tuple[float, list[str]] | None = None
        self.connected = False
        self.mode = "idle"  # idle / ws_v2 / ws_v1
        self._symbols_seen: set[str] = set()

    @property
    def upstream_symbols(self) -> int:
        return len(self._symbols_seen)

    def _next_version(self) -> int:
        """本轮回合的 WS 版本：RELAY_WS_VERSION=1 固定 v1（套餐暂不含
        v2 时不做无谓探测）；auto 先 v2、冷却期内 v1"""
        if self._settings.ws_version == "1":
            return 1
        return 2 if time.monotonic() >= self._v2_cooldown_until else 1

    @staticmethod
    def _ws_url_for(base: str, version: int) -> str:
        """v2 用配置原值；v1 由 v2 地址推导（与 ws_bridge 同款）"""
        if version == 2:
            return base
        if "/v2/" in base:
            return base.replace("/v2/", "/v1/")
        return "wss://rest.vvtr.com/v1/connect"

    async def run(self) -> None:
        """supervisor：预检 → 订阅 → 会话；禁连/换月/断线各有出路"""
        backoff = 1.0
        while True:
            await self._gate.wait_until_clear()
            symbols = await self._current_subscription()
            version = self._next_version()
            started = time.monotonic()
            outcome = await self._session(version, symbols)
            if outcome == "resubscribe":
                self._resubscribe.clear()
                logger.info("订阅清单已变化，按新清单立即重连")
                continue
            if outcome == "forbidden":
                if version == 1:
                    # v1 也被拒（key 权限/有效期异常）：退避重试防热循环
                    logger.warning(
                        "上游 v1 被拒（403），key 权限/有效期异常，%.0fs 后重试",
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 60.0)
                    continue
                self._v2_cooldown_until = time.monotonic() + V2_RETRY_INTERVAL
                logger.warning(
                    "上游 v2 深度被拒（403），切 v1；%.0f 分钟后重试 v2",
                    V2_RETRY_INTERVAL / 60,
                )
                continue
            if time.monotonic() - started > 60.0:
                backoff = 1.0  # 会话健康存活过，断线后从最短退避重来
            logger.info("上游断开，%.0fs 后重连", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    async def _session(self, version: int, symbols: list[str]) -> str:
        """单次上游连接生命周期；返回 disconnected / forbidden / resubscribe"""
        import websockets

        params = (
            f"apiKey={self._settings.api_key}&type=14&symbols={','.join(symbols)}"
        )
        url = f"{self._ws_url_for(self._settings.upstream_ws, version)}?{params}"
        self.mode = f"ws_v{version}"
        self._symbols_seen = set()
        logger.info("上游 WS(v%d) 连接中…（订阅 %d 个合约）", version, len(symbols))
        try:
            async with websockets.connect(
                url, max_size=8 * 1024 * 1024, ping_interval=50, ping_timeout=20
            ) as ws:
                self.connected = True
                logger.info("上游 WS(v%d) 已连接", version)
                ping_task = asyncio.create_task(self._ping_loop(ws))
                watch_task = asyncio.create_task(self._resub_watch(ws))
                try:
                    async for message in ws:
                        await self._on_frame(message)
                finally:
                    for task in (ping_task, watch_task):
                        task.cancel()
                    for task in (ping_task, watch_task):
                        with contextlib.suppress(asyncio.CancelledError):
                            await task
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            text = str(exc)
            if "403" in text or "Forbidden" in text:
                return "forbidden"
            logger.warning("上游 WS(v%d) 断开：%s", version, text)
        finally:
            self.connected = False
        if self._resubscribe.is_set():
            return "resubscribe"
        return "disconnected"

    async def _ping_loop(self, ws: Any) -> None:
        while True:
            await asyncio.sleep(45)
            with contextlib.suppress(Exception):
                await ws.send("ping")

    async def _resub_watch(self, ws: Any) -> None:
        """清单刷新任务置位事件 → 主动断开当前会话（订阅覆盖式，须重连）"""
        await self._resubscribe.wait()
        with contextlib.suppress(Exception):
            await ws.close()

    async def _on_frame(self, raw: str | bytes) -> None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        text = raw.strip()
        if not text or text.startswith("pong"):
            return
        self._stats.frames_upstream += 1
        frames = parse_vvtr_frames(text)
        # 剥层统计订阅覆盖
        if len(self._symbols_seen) < 2 * SUBSCRIBE_CAP:
            for msg in frames:
                sym = str(msg.get("s") or "").strip().lower()
                if sym:
                    self._symbols_seen.add(sym)
        # v1 上游：翻成 v2 帧形再广播（下游桥对配置 ws_url 一律按 v2
        # 会话解析，原样透传会丢 o/h/l/cV/cP/cA/盘口）；v2 原样透传
        if self.mode == "ws_v1" and frames:
            payload = json.dumps(
                [translate_v1_frame(m) for m in frames],
                ensure_ascii=False,
                separators=(",", ":"),
            )
        else:
            payload = text
        sent = self._hub.broadcast(payload)
        if sent:
            self._stats.frames_broadcast += sent

    async def _current_subscription(self) -> list[str]:
        # 主力基线 + 附加观察合约（去重/有效清单过滤/cap 交给 build_subscription）
        symbols = [s for s in self._masters.values() if s]
        symbols.extend(s for s in self._extras if s)
        valid = await self._valid_symbols()
        return build_subscription(symbols, valid)

    async def _valid_symbols(self) -> list[str]:
        """VVTR /symbols 有效清单（进程内缓存 10 分钟；失败回退空表不过滤）"""
        now = time.monotonic()
        if self._valid_cache and now - self._valid_cache[0] < VALID_SYMBOLS_TTL:
            return self._valid_cache[1]
        from asyncio import to_thread

        from app.connectors.vvtr.http_client import fetch_vvtr_symbols

        try:
            valid = await to_thread(
                fetch_vvtr_symbols, self._settings.api_key, self._settings.upstream_rest
            )
            self._rate.note()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("relay /symbols 拉取失败", exc_info=True)
            return self._valid_cache[1] if self._valid_cache else []
        if valid:
            self._valid_cache = (now, valid)
        return valid

    async def refresh_loop(self) -> None:
        """每 12h 刷新主力（9999）；清单变化 → 置位重连事件"""
        while True:
            await asyncio.sleep(MASTER_REFRESH_INTERVAL)
            try:
                if await self._refresh_masters():
                    self._resubscribe.set()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("relay 主力刷新失败")

    async def _cached_real_symbol(self, code: str, suffix: str = "9999") -> str:
        """realSymbol 走与下游代理同款共键缓存：条目共享，出网只花一次

        429 防护窗内不出网（返回空串，本轮扫表放弃该品种）；真实出网后
        慢进 _REAL_SYMBOL_SWEEP_GAP 秒——内部扫表与 K 线流量共享 1rps
        预算，持续贴满会触发 VVTR key 级限频（有黑名单风险）。
        """
        from app.connectors.vvtr.http_client import real_symbol_params

        params = real_symbol_params(code.lower(), suffix)
        key = cache_key("realSymbol", params)
        hit = self._cache.get(key)
        if hit is not None:
            return _parse_real_symbol_body(hit.body)
        if self._pacing.active():
            return ""
        result = await asyncio.to_thread(
            _forward_sync, self._settings, "realSymbol", params, self._rate
        )
        body_code = _body_code(result.body)
        if result.status == 429 or body_code == 429:
            self._stats.upstream_429 += 1
            self._pacing.note_429()
            logger.error(
                "VVTR 上游 429（realSymbol 内部刷新 %s）——key 级限频", code
            )
            return ""
        if result.status == 200 and body_code == 200:
            self._cache.put(key, result, _PROXY_TTLS["realSymbol"])
        await asyncio.sleep(_REAL_SYMBOL_SWEEP_GAP)
        return _parse_real_symbol_body(result.body)

    async def _refresh_masters(self) -> bool:
        fetched: dict[str, str] = {}
        for code in list(self._masters):
            if self._pacing.active():
                logger.warning(
                    "realSymbol 429 防护窗内，中止本轮主力扫表（下轮重试），已完成 %d 品种",
                    len(fetched),
                )
                break
            try:
                rsym = await self._cached_real_symbol(code, "9999")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("relay realSymbol(%s) 查询失败", code, exc_info=True)
                continue
            if rsym:
                fetched[code] = rsym
        updated = apply_master_refresh(self._masters, fetched)
        if updated == self._masters:
            return False
        changes = {
            code: (self._masters.get(code, ""), sym)
            for code, sym in updated.items()
            if self._masters.get(code, "") != sym
        }
        self._masters = updated
        logger.info(
            "relay 主力刷新：%s",
            ", ".join(f"{c}:{old or '无'}→{new}" for c, (old, new) in changes.items()),
        )
        return True


# ---------- HTTP 代理 ----------


def _body_code(body: bytes) -> Any:
    try:
        data = json.loads(body)
    except Exception:
        return None
    return data.get("code") if isinstance(data, dict) else None


def _forward_sync(
    settings: RelaySettings, sub: str, params: dict[str, str], rate: RateTracker
) -> CachedResponse:
    """同步线程执行：注入真钥 → 全局 1 rps 节流 → GET 上游（失败 502）"""
    merged = dict(params)
    merged["apiKey"] = settings.api_key
    url = settings.upstream_rest.rstrip("/") + "/" + sub
    rate.note()
    _throttle()
    try:
        resp = httpx.get(url, params=merged, timeout=15)
    except Exception as exc:
        logger.warning("relay 转发失败 %s：%s", sub, exc)
        return CachedResponse(
            502, "application/json", b'{"code":502,"msg":"relay: upstream request failed"}'
        )
    ctype = resp.headers.get("content-type") or "application/json"
    logger.debug("relay → %s %s（参数已去钥）", sub, params)
    return CachedResponse(resp.status_code, ctype, resp.content)


def _parse_real_symbol_body(body: bytes) -> str:
    """realSymbol 响应体 → 裸合约（倒序取最新 rsymbol，剥交易所前缀）

    与 http_client.fetch_vvtr_real_master 的解析同构；不可解析返回空串。
    """
    try:
        data = json.loads(body)
    except Exception:
        return ""
    rows = (data or {}).get("data") or []
    for row in reversed(rows):
        rsym = str(row.get("rsymbol") or "").strip().lower().split(".")[-1]
        if rsym:
            return rsym
    return ""


async def _proxy(ctx: "RelayContext", sub: str, request: Request) -> Response:
    # 下游鉴权：RELAY_TOKEN 启用后，apiKey 参数须为 token（或真钥）；
    # 通过后照旧剥钥——转发上游一律注入容器 env 真钥
    downstream_key = ""
    for k, v in request.query_params.items():
        if str(k).strip().lower() == "apikey":
            downstream_key = str(v)
            break
    if not authorized(ctx.settings, downstream_key):
        return Response(
            content=b'{"code":401,"msg":"relay: unauthorized"}',
            status_code=401,
            media_type="application/json",
        )
    params = strip_api_key(dict(request.query_params))
    # fresh=1：人工核对用穿透——绕过缓存读直取上游并回写（realSymbol
    # 6h 长缓存下核对"现在的主力"必须能穿透）；不参与缓存键、不转发上游
    fresh = str(params.pop("fresh", "")).strip().lower() in ("1", "true", "yes")
    key = cache_key(sub, _cache_key_params(sub, params))
    hit = None if fresh else ctx.cache.get(key)
    if hit is not None:
        ctx.stats.cache_hits += 1
        return Response(
            content=hit.body, status_code=hit.status, media_type=hit.content_type
        )
    # 单飞：并发同参共享一次出网（压测放大器——同参请求各自排队
    # 各耗 1rps 预算；共享后只花一次）
    fut = ctx.inflight.get(key)
    if fut is not None:
        ctx.stats.singleflight_hits += 1
        result = await asyncio.shield(fut)
        return Response(
            content=result.body,
            status_code=result.status,
            media_type=result.content_type,
        )
    # 429 防护窗口：超频风暴期间直接 503，不出网（保 key 不进黑名单）
    if ctx.pacing.active():
        ctx.stats.pacing_rejects += 1
        return Response(
            content=b'{"code":503,"msg":"relay: pacing window"}',
            status_code=503,
            media_type="application/json",
        )
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    ctx.inflight[key] = fut
    try:
        try:
            result = await asyncio.to_thread(
                _forward_sync, ctx.settings, sub, params, ctx.rate
            )
        except asyncio.CancelledError:
            # 持有者被取消：给等待方一个确定的 502，不让它们悬空
            if not fut.done():
                fut.set_result(
                    CachedResponse(
                        502,
                        "application/json",
                        b'{"code":502,"msg":"relay: internal error"}',
                    )
                )
            raise
        except Exception:
            logger.exception("relay 转发异常 %s", sub)
            result = CachedResponse(
                502, "application/json", b'{"code":502,"msg":"relay: internal error"}'
            )
    finally:
        ctx.inflight.pop(key, None)
    if not fut.done():
        fut.set_result(result)
    ctx.stats.http_forwarded += 1
    code = _body_code(result.body)
    if result.status == 429 or code == 429:
        ctx.stats.upstream_429 += 1
        ctx.pacing.note_429()
        logger.error("VVTR 上游 429（%s）——key 级限频，检查消费端请求节奏", sub)
    elif result.status == 200 and code == 200:
        ctx.cache.put(key, result, _PROXY_TTLS[sub])
    return Response(
        content=result.body, status_code=result.status, media_type=result.content_type
    )


# ---------- 应用装配 ----------


@dataclass
class RelayContext:
    settings: RelaySettings
    hub: DownstreamHub = field(default_factory=DownstreamHub)
    stats: RelayStats = field(default_factory=RelayStats)
    cache: TTLCache = field(default_factory=TTLCache)
    rate: RateTracker = field(default_factory=RateTracker)
    gate: PeerGate | None = None
    upstream: UpstreamManager | None = None
    pacing: PacingWindow = field(default_factory=PacingWindow)
    inflight: dict[str, asyncio.Future] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.gate is None:
            self.gate = PeerGate(self.settings.peer_redis_url, self.settings.skip_peer_check)
        if self.upstream is None:
            self.upstream = UpstreamManager(
                self.settings,
                self.hub,
                self.stats,
                self.rate,
                self.gate,
                cache=self.cache,
                pacing=self.pacing,
            )


def create_app(
    settings: RelaySettings | None = None, start_upstream: bool = True
) -> FastAPI:
    """组装 relay 应用；start_upstream=False 供测试只验 HTTP 面"""
    ctx = RelayContext(settings or RelaySettings.from_env())

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        tasks: list[asyncio.Task] = []
        if start_upstream and ctx.upstream is not None:
            tasks.append(asyncio.create_task(ctx.upstream.run(), name="relay-upstream"))
            tasks.append(
                asyncio.create_task(ctx.upstream.refresh_loop(), name="relay-refresh")
            )
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    app = FastAPI(title="qihuo-vvtr-relay", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.relay = ctx

    @app.get("/health")
    async def health() -> dict[str, Any]:
        up = ctx.upstream
        return {
            "upstream_connected": bool(up and up.connected),
            "upstream_mode": up.mode if up else "disabled",
            "downstream_clients": ctx.hub.client_count,
            "upstream_symbols": up.upstream_symbols if up else 0,
            "cache_size": len(ctx.cache),
            "throttled_rps": ctx.rate.rps(),
        }

    @app.get("/stats")
    async def stats() -> dict[str, int]:
        return ctx.stats.snapshot()

    def proxy_route(sub: str):
        async def handler(request: Request) -> Response:
            return await _proxy(ctx, sub, request)

        return handler

    for sub in _PROXY_TTLS:
        app.add_api_route(f"/{sub}", proxy_route(sub), methods=["GET"])
        app.add_api_route(f"/v1/{sub}", proxy_route(sub), methods=["GET"])

    def ws_connect():
        async def endpoint(websocket: WebSocket) -> None:
            apikey = websocket.query_params.get("apiKey", "")
            symbols = websocket.query_params.get("symbols", "")
            # RELAY_TOKEN 启用后校验下游凭证（未配置保持内网信任放行）
            if not authorized(ctx.settings, apikey):
                await websocket.accept()
                await websocket.close(code=4401, reason="unauthorized")
                logger.warning("下游 WSS 鉴权失败，已拒绝（symbols=%s）", symbols[:80])
                return
            await websocket.accept()
            # Phase 1 内网信任：apiKey 仅脱敏记录不校验（计划 4.4：传 token 或任意值均可）
            logger.info(
                "下游 WSS 接入：apiKey=%s（symbols=%s）",
                _mask({"apiKey": apikey})["apiKey"],
                symbols[:80],
            )
            client = ctx.hub.register()
            sender = asyncio.create_task(_client_sender(websocket, client))
            try:
                await websocket.send_text(
                    json.dumps(build_welcome_frame(), ensure_ascii=False)
                )
                while True:
                    msg = await websocket.receive_text()
                    if str(msg).strip() == "ping":
                        with contextlib.suppress(Exception):
                            await websocket.send_text("pong")
            except WebSocketDisconnect:
                pass
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("下游连接结束", exc_info=True)
            finally:
                ctx.hub.unregister(client)
                sender.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await sender
                logger.info("下游 WSS 断开（剩余 %d 个）", ctx.hub.client_count)

        return endpoint

    app.add_api_websocket_route("/connect", ws_connect())
    app.add_api_websocket_route("/v1/connect", ws_connect())

    return app


class _WsAcceptLogFilter(logging.Filter):
    """uvicorn 的 WS accept/断开日志带完整 query（含下游 apiKey），
    且不受 access_log=False 控制（websockets_impl 走 uvicorn.error
    logger，2026-08-25 58 容器日志实测泄漏）——直接过滤丢弃。"""

    # accept/403/断开三种 WS 日志格式都含 '"WebSocket %s"'（带完整 query）
    _MARKS = ('"WebSocket ',)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        return not any(mark in msg for mark in self._MARKS)


def install_ws_log_filter() -> None:
    """幂等安装 WS 访问日志过滤（relay main() 启动时调用）"""
    lg = logging.getLogger("uvicorn.error")
    if any(isinstance(f, _WsAcceptLogFilter) for f in lg.filters):
        return
    lg.addFilter(_WsAcceptLogFilter())


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # httpx INFO 会打印含 apiKey 的完整 URL，必须压级（密钥不进日志）
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    # uvicorn WS accept 日志同样带完整 query（含下游 apiKey），过滤丢弃
    install_ws_log_filter()
    settings = RelaySettings.from_env()
    if not settings.api_key:
        logger.error("缺少 VVTR_API_KEY（relay.env 未挂载？），拒绝启动")
        raise SystemExit(2)
    import uvicorn

    uvicorn.run(
        create_app(settings),
        host="0.0.0.0",
        port=settings.listen_port,
        log_level="info",
        access_log=False,  # 访问日志含完整 URL（下游 apiKey），密钥不进日志；统计走 /stats
    )


if __name__ == "__main__":
    main()
