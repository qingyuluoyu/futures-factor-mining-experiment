"""sina 桥 K 线守卫：缓存 + 单飞 + 令牌桶 + 熔断

2026-08-25 压测结论（vvtr-relay-stress/sina_bridge_stress.py）：
- 单人随意切换 p50 0.79s；6 并发正常（3.7rps）；
- **12 并发=悬崖**——新浪 K线 jsonp 接口按 IP 限流，150/150 全空，
  整源封禁 6-7 分钟。桥此前无任何保护（无缓存/无合并/无限速/无帽）。

守卫语义（openspec sina-bridge-protection）：
- 缓存：minute TTL 60s / daily TTL 300s；干净空结果短缓存 20s
  （死合约不反复出网）；保留最后一份非空「过期副本」仅供熔断兜底；
- 单飞：同 key 并发共享一次出网；
- 令牌桶：出网间隔 ≥250ms（4rps）+ 专用线程池 6 并发帽，
  排队超 8s 返回过期副本或 503；
- 熔断：连续上游故障（异常，非干净空）≥10 次 → 暂停出网 30s，
  探针仍失败按 60s/120s/300s 递增；期间返回过期副本（X-Bridge-Stale: 1）。
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.data import sina_kline

logger = logging.getLogger("qihuo.sina_bridge")

# 分钟周期 TTL（bar 变化频率 = 1根/period，60s 足够新鲜；形成中 bar 由 WS 推）
MINUTE_TTL = 60.0
DAILY_TTL = 300.0
# 干净空结果（合约无数据）的短缓存：防死合约反复出网
EMPTY_TTL = 20.0
# 出网节奏：≥250ms 间隔（4rps，压测 3.7rps 安全 / ~6rps 触发悬崖之间）
OUTBOUND_INTERVAL = 0.25
# 出网并发帽（专用线程池大小；压测 6 并发安全）
OUTBOUND_CONCURRENCY = 6
# 排队预算：超时返回过期副本或 503
QUEUE_TIMEOUT = 8.0
# 单次出网自身预算（内部 httpx 超时 10-15s，再留余量）
FETCH_TIMEOUT = 25.0
# 缓存条目上限（69 主力 × 8 周期远用不满，防长尾合约撑爆内存）
CACHE_MAX = 512
# 熔断：连续上游故障阈值 + 暂停时长阶梯（探针失败递增，上限 5min）
BREAKER_THRESHOLD = 10
BREAKER_DURATIONS = (30.0, 60.0, 120.0, 300.0)


class BridgeBusy(Exception):
    """出网排队超预算（令牌桶积压），应降级过期副本或 503"""


class BridgeUpstreamError(Exception):
    """上游故障（HTTP/解析/连接异常），计入熔断；干净空结果不算"""


class KlineGuard:
    """K 线端点守卫（每应用一个实例，事件循环内使用）"""

    def __init__(self) -> None:
        self._fresh: OrderedDict[tuple[str, str, str], tuple[list[dict[str, Any]], float]] = OrderedDict()
        self._stale: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        self._inflight: dict[tuple[str, str, str], asyncio.Future] = {}
        self._pace_lock = asyncio.Lock()
        self._last_dispatch = 0.0
        self._pool = ThreadPoolExecutor(
            max_workers=OUTBOUND_CONCURRENCY, thread_name_prefix="sina-out"
        )
        self._cont_fails = 0
        self._breaker_until = 0.0
        self._breaker_stage = 0
        # 观测计数（/stats 暴露）
        self.kline_cache_hits = 0
        self.kline_upstream = 0
        self.kline_upstream_fail = 0
        self.kline_singleflight = 0
        self.kline_queue_reject = 0
        self.kline_breaker_serves = 0

    # ---------- 对外 ----------

    async def fetch(self, kind: str, symbol: str, period: str = "") -> tuple[list[dict[str, Any]], bool]:
        """取 K 线。返回 (bars, stale)；stale=True 表示过期副本兜底。

        抛 BridgeBusy（排队超预算）/ BridgeUpstreamError（上游故障）。
        """
        key = (kind, str(symbol).strip().upper(), period)
        now = time.monotonic()
        entry = self._fresh.get(key)
        if entry is not None:
            if entry[1] > now:
                self._fresh.move_to_end(key)
                self.kline_cache_hits += 1
                return entry[0], False
            self._fresh.pop(key, None)

        # 熔断中：不出网，过期副本兜底
        if now < self._breaker_until:
            stale = self._stale.get(key)
            if stale is not None:
                self.kline_breaker_serves += 1
                return stale, True
            raise BridgeUpstreamError("breaker open (no stale copy)")

        # 单飞：并发同 key 共享一次出网
        fut = self._inflight.get(key)
        if fut is not None:
            self.kline_singleflight += 1
            return await self._share(fut)

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._inflight[key] = fut
        try:
            payload = await self._outbound_owned(loop, key, kind, symbol, period)
            if not fut.done():
                fut.set_result(payload)
            return payload, False
        except asyncio.CancelledError:
            if not fut.done():
                fut.set_exception(BridgeBusy("owner cancelled"))
            raise
        except BaseException as exc:
            if not fut.done():
                fut.set_exception(exc)
            raise
        finally:
            self._inflight.pop(key, None)

    def stale_copy(self, kind: str, symbol: str, period: str = "") -> list[dict[str, Any]] | None:
        key = (kind, str(symbol).strip().upper(), period)
        return self._stale.get(key)

    def snapshot(self) -> dict[str, Any]:
        return {
            "kline_cache_hits": self.kline_cache_hits,
            "kline_upstream": self.kline_upstream,
            "kline_upstream_fail": self.kline_upstream_fail,
            "kline_singleflight": self.kline_singleflight,
            "kline_queue_reject": self.kline_queue_reject,
            "kline_breaker_serves": self.kline_breaker_serves,
            "continuous_fail": self._cont_fails,
            "breaker_state": "open" if time.monotonic() < self._breaker_until else "closed",
        }

    async def aclose(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ---------- 内部 ----------

    async def _share(self, fut: asyncio.Future) -> tuple[list[dict[str, Any]], bool]:
        payload = await asyncio.shield(fut)
        return payload, False

    async def _outbound_owned(
        self,
        loop: asyncio.AbstractEventLoop,
        key: tuple[str, str, str],
        kind: str,
        symbol: str,
        period: str,
    ) -> list[dict[str, Any]]:
        deadline = time.monotonic() + QUEUE_TIMEOUT
        # 令牌桶：全局出网间隔（异步排队，不占线程）
        try:
            await asyncio.wait_for(
                self._pace_lock.acquire(), timeout=max(0.1, deadline - time.monotonic())
            )
        except asyncio.TimeoutError:
            self.kline_queue_reject += 1
            raise BridgeBusy("pace queue timeout")
        try:
            wait = OUTBOUND_INTERVAL - (time.monotonic() - self._last_dispatch)
            if wait > 0:
                await asyncio.sleep(wait)
            if time.monotonic() > deadline:
                self.kline_queue_reject += 1
                raise BridgeBusy("pace queue timeout")
            self._last_dispatch = time.monotonic()
        finally:
            self._pace_lock.release()

        if kind == "minute":
            fn = functools.partial(sina_kline.fetch_minute_kline_strict, symbol, period)
        else:
            fn = functools.partial(sina_kline.fetch_daily_kline_strict, symbol)
        self.kline_upstream += 1
        try:
            payload = await asyncio.wait_for(
                loop.run_in_executor(self._pool, fn), timeout=FETCH_TIMEOUT
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._note_fail()
            raise BridgeUpstreamError(f"{type(exc).__name__}: {exc}") from exc

        # 干净响应（含空=合约无数据）：证明上游存活，重置熔断计数
        self._cont_fails = 0
        self._breaker_stage = 0
        self._breaker_until = 0.0
        ttl = (MINUTE_TTL if kind == "minute" else DAILY_TTL) if payload else EMPTY_TTL
        self._store(key, payload, ttl)
        return payload

    def _store(
        self,
        key: tuple[str, str, str],
        payload: list[dict[str, Any]],
        ttl: float,
    ) -> None:
        self._fresh[key] = (payload, time.monotonic() + ttl)
        self._fresh.move_to_end(key)
        while len(self._fresh) > CACHE_MAX:
            self._fresh.popitem(last=False)
        if payload:
            self._stale[key] = payload

    def _note_fail(self) -> None:
        self.kline_upstream_fail += 1
        self._cont_fails += 1
        if self._cont_fails >= BREAKER_THRESHOLD:
            dur = BREAKER_DURATIONS[min(self._breaker_stage, len(BREAKER_DURATIONS) - 1)]
            self._breaker_stage += 1
            self._breaker_until = time.monotonic() + dur
            logger.warning(
                "sina 桥熔断开启 %.0fs（连续上游故障 %d 次；12 并发压测悬崖防复现）",
                dur,
                self._cont_fails,
            )
