"""VVTR HTTP 客户端 —— 跨进程 1 req/s 节流 + 快照兜底 + 套餐查询 + 历史K线

VVTR HTTP 硬限频 1 次/秒（key 级，多次超频进临时黑名单），因此本模块
所有 HTTP 请求共用节流器：Redis SET NX PX 为主（backend 多 worker 与
一次性回填脚本各自独立进程，进程内锁管不住彼此，必须经 Redis 共享
1 rps 预算），进程内时间戳兜底（Redis 不可用时退化为单进程节流）。
调用方均跑在 to_thread 的独立线程（sync 函数），节流器用 threading 实现。
重试策略遵循官方建议：失败等 2s 再试，最多 3 次（计入节流）。
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from app.connectors.vvtr.quote_map import vvtr_snapshot_to_quote

logger = logging.getLogger("qihuo.vvtr.http")

_BJ_TZ = timezone(timedelta(hours=8))

# 官方限频 1 次/秒 → 留 10% 余量
_MIN_REQUEST_INTERVAL = 1.1
_RETRY_DELAYS = (2.0, 4.0)  # 首次失败后再试 2 次

# 跨进程节流：抢到锁键才放行本次请求（TTL = 限频间隔）
_REDIS_THROTTLE_KEY = "vvtr:http:throttle"
_REDIS_THROTTLE_MS = int(_MIN_REQUEST_INTERVAL * 1000)
_THROTTLE_WAIT_MAX = 30.0  # 多进程竞争下抢锁最长等待（防饿死死等）

_throttle_lock = threading.Lock()
_last_request_ts = 0.0
_throttle_client: Any = None


def _throttle_redis():
    """惰性初始化的 sync Redis 客户端；不可用返回 None（退回进程内节流）"""
    global _throttle_client
    if _throttle_client is not None:
        return _throttle_client or None
    with _throttle_lock:
        if _throttle_client is None:
            try:
                import redis as redis_sync

                from app.core.config import load_settings

                s = load_settings()
                client = redis_sync.Redis(
                    host=s.redis_host,
                    port=s.redis_port,
                    db=s.redis_db,
                    socket_timeout=2,
                    socket_connect_timeout=2,
                )
                client.ping()
                _throttle_client = client
            except Exception:
                logger.debug("VVTR Redis 节流不可用，退回进程内节流", exc_info=True)
                _throttle_client = False
    return _throttle_client or None


def _throttle_local() -> None:
    """进程内兜底节流：本进程任意两次请求间隔 ≥ 1.1s"""
    global _last_request_ts
    while True:
        with _throttle_lock:
            now = time.monotonic()
            wait = _MIN_REQUEST_INTERVAL - (now - _last_request_ts)
            if wait <= 0:
                _last_request_ts = now
                return
        time.sleep(min(wait, _MIN_REQUEST_INTERVAL))


def _throttle() -> None:
    """全局串行节流：Redis 锁（跨进程）→ 进程内时间戳（兜底）"""
    global _last_request_ts
    client = _throttle_redis()
    if client is not None:
        deadline = time.monotonic() + _THROTTLE_WAIT_MAX
        while time.monotonic() < deadline:
            try:
                if client.set(
                    _REDIS_THROTTLE_KEY, "1", nx=True, px=_REDIS_THROTTLE_MS
                ):
                    with _throttle_lock:
                        _last_request_ts = time.monotonic()
                    return
            except Exception:
                break  # Redis 抖动 → 进程内兜底
            time.sleep(0.1)
        # 超时未抢到（极端竞争）也走兜底，宁可排队不可超频
    _throttle_local()


_OFFICIAL_HOST = "rest.vvtr.com"


def throttle_applies(base_url: str) -> bool:
    """客户端节流仅在直连官方时生效；经 relay 由 relay 统一执行预算。

    双重节流实测代价：backend 与 relay 抢同一把 Redis 锁，每个上游
    请求要排两次队（≥2.2s），分时 K 线冷切换 4-5s。base_url 指向
    relay（任何非官方主机）时跳过客户端节流，预算由 relay 单点执行。
    """
    base = (base_url or "").strip()
    if not base:
        return True  # 未指定 → 默认官方
    try:
        host = urlparse(base).hostname or ""
    except ValueError:
        return True
    return host.lower() == _OFFICIAL_HOST


def _request(
    base_url: str,
    path: str,
    params: dict[str, Any],
) -> dict[str, Any] | None:
    """节流 GET；返回解析后的 JSON，失败返回 None（不抛出）"""
    url = base_url.rstrip("/") + path
    local_throttle = throttle_applies(base_url)
    # 经 relay 时改用 RELAY_TOKEN 作下游凭证（relay 剥钥后注入容器真钥）；
    # 直连官方仍用真钥。未配置 token 时保持原值，relay 兼容放行。
    params = dict(params)
    if "apiKey" in params:
        relay_token = os.getenv("VVTR_RELAY_TOKEN", "").strip()
        if relay_token and not local_throttle:
            params["apiKey"] = relay_token
    last_error = ""
    for attempt in range(1 + len(_RETRY_DELAYS)):
        if local_throttle:
            _throttle()
        try:
            resp = httpx.get(url, params=params, timeout=15)
            payload = resp.json()
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            payload = None
        if isinstance(payload, dict):
            code = payload.get("code")
            if code == 200:
                return payload
            # 401/403/429 等业务错误不重试（key 无效/超频重试无意义）
            if code in (401, 403):
                logger.warning("VVTR %s 拒绝：code=%s msg=%s", path, code, payload.get("msg"))
                return None
            last_error = f"code={code} msg={payload.get('msg')}"
        elif payload is not None:
            last_error = "非 JSON 响应"
        if attempt < len(_RETRY_DELAYS):
            time.sleep(_RETRY_DELAYS[attempt])
    logger.warning("VVTR %s 请求失败：%s", path, last_error)
    return None


def _mask(params: dict[str, Any]) -> dict[str, Any]:
    """日志/异常安全：apiKey 一律脱敏"""
    out = dict(params)
    if "apiKey" in out:
        key = str(out["apiKey"] or "")
        out["apiKey"] = f"{key[:3]}***" if len(key) > 6 else "***"
    if "apikey" in out:
        key = str(out["apikey"] or "")
        out["apikey"] = f"{key[:3]}***" if len(key) > 6 else "***"
    return out


def fetch_vvtr_symbols(
    api_key: str,
    base_url: str = "",
    type_code: int = 14,
) -> list[str]:
    """GET /symbols → 当日有效合约列表（大写；免费基础接口，每日盘前更新）

    订阅前用它过滤主力列表，剔除已下市合约（无效 symbol 会毒化
    整个 WSS 订阅导致零推送）。失败返回空列表，调用方回退不过滤。
    """
    from app.connectors.vvtr.config import DEFAULT_BASE_URL

    if not api_key:
        return []
    base = (base_url or DEFAULT_BASE_URL).strip()
    payload = _request(base, "/symbols", {"type": type_code, "apiKey": api_key})
    if not payload:
        return []
    out: list[str] = []
    for item in payload.get("data") or []:
        if isinstance(item, dict):
            sym = str(item.get("symbol") or "").strip()
            if sym:
                out.append(sym.upper())
    return out


def real_symbol_params(product: str, suffix: str = "9999") -> dict[str, str]:
    """realSymbol 查询参数（不含 apiKey）：symbol + 近两交易日窗口

    fetch_vvtr_real_master 与 relay 内部主力刷新共用——后者与下游代理
    按同参共键缓存，参数构造必须严格一致否则缓存键漂移。
    """
    from datetime import datetime, timedelta, timezone

    bj = datetime.now(timezone(timedelta(hours=8)))
    return {
        "symbol": f"{product}{suffix}",
        "from": (bj - timedelta(days=1)).strftime("%Y-%m-%d"),
        "to": bj.strftime("%Y-%m-%d"),
    }


def fetch_vvtr_real_master(
    api_key: str,
    product: str,
    base_url: str = "",
    suffix: str = "9999",
) -> str:
    """GET /realSymbol → 品种真实合约（product=pp → pp2701）

    suffix：9999 主力（默认）/ 9998 次主力。from/to 必须传非空日期
    （实测空串报 400 "from参数不能为空"），取近两个交易日窗口取最新
    映射。失败/无数据返回空串。
    """
    from app.connectors.vvtr.config import DEFAULT_BASE_URL

    if not api_key or not product:
        return ""
    base = (base_url or DEFAULT_BASE_URL).strip()
    payload = _request(
        base,
        "/realSymbol",
        {
            **real_symbol_params(product, suffix),
            "apiKey": api_key,
        },
    )
    rows = (payload or {}).get("data") or []
    for row in reversed(rows):
        # rsymbol 形如 "DCE.pp2701"（带交易所前缀）→ 剥前缀取裸合约
        rsym = str(row.get("rsymbol") or "").strip().lower().split(".")[-1]
        if rsym:
            return rsym
    return ""


def fetch_vvtr_snapshot(
    api_key: str,
    symbols: list[str],
    base_url: str = "",
    type_code: int = 14,
) -> list[dict[str, Any]]:
    """HTTP /snapshot 批量快照 → 统一 quote 列表（WS 断连兜底，≤1 rps）"""
    if not api_key or not symbols:
        return []
    from app.connectors.vvtr.config import DEFAULT_BASE_URL

    base = (base_url or DEFAULT_BASE_URL).strip()
    sym_arg = ",".join(dict.fromkeys(s.strip().lower() for s in symbols if s.strip()))
    payload = _request(
        base,
        "/snapshot",
        {"type": type_code, "symbols": sym_arg, "apiKey": api_key},
    )
    if not payload:
        return []
    out: list[dict[str, Any]] = []
    for snap in payload.get("data") or []:
        quote = vvtr_snapshot_to_quote(snap)
        if quote is not None:
            out.append(quote)
    return out


def fetch_vvtr_permissions(
    api_key: str,
    mobile: str = "",
    base_url: str = "",
) -> dict[str, Any]:
    """GET /my/permissions → 套餐/权限摘要（连接测试用，不抛出）

    返回 {ok, code, msg, packages, apis}；ok=False 时 msg 说明原因。
    """
    from app.connectors.vvtr.config import DEFAULT_BASE_URL

    base = (base_url or DEFAULT_BASE_URL).strip()
    params: dict[str, Any] = {"apikey": api_key}
    if mobile:
        params["mobile"] = mobile
    try:
        payload = _request(base, "/my/permissions", params)
    except Exception:
        logger.exception("VVTR 权限查询异常")
        payload = None
    if not payload:
        return {"ok": False, "code": None, "msg": "请求失败（网络/限频）", "packages": [], "apis": []}
    code = payload.get("code")
    data = payload.get("data") or {}
    packages = [
        {
            "name": p.get("name"),
            "market_code": p.get("market_code"),
            "market_name": p.get("market_name"),
            "limit": p.get("limit"),
            "expire_time": p.get("expire_time"),
            "days_left": p.get("days_left"),
        }
        for p in (data.get("active_packages") or [])
        if isinstance(p, dict)
    ]
    apis: list[str] = []
    for group in data.get("owned_api_groups") or []:
        if not isinstance(group, dict):
            continue
        for api in group.get("apis") or []:
            if isinstance(api, dict) and api.get("path"):
                apis.append(str(api["path"]))
    return {
        "ok": code == 200,
        "code": code,
        "msg": payload.get("msg") or ("ok" if code == 200 else "查询失败"),
        "packages": packages,
        "apis": sorted(set(apis)),
    }


# ---------- 历史K线 ----------

# 内部 period → VVTR interval（大小写敏感）
_PERIOD_TO_INTERVAL: dict[str, str] = {
    "1m": "1m",
    "3m": "3m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "60m": "1h",
    "1h": "1h",
    "1d": "1D",
    "1D": "1D",
    "1w": "1W",
    "1W": "1W",
}

# 各周期数据窗口上限（官方：<15m 仅近 6 个月；15m~1d 近 2 年）
_SUB15_WINDOW_DAYS = 180
_MINUTE_WINDOW_DAYS = 730
_DAILY_WINDOW_DAYS = 3650  # 国内期货到 2005 年，取 10 年窗口


def _bar_time(bar: dict[str, Any]) -> str:
    """VVTR b（bob，bar 起点）→ 'YYYY-MM-DD HH:MM:SS'（日线由调用方截断为日期）"""
    raw = str(bar.get("b") or "").strip()
    if not raw:
        raw = str(bar.get("e") or "").strip()
    return raw[:19]


def parse_history_response(payload: dict[str, Any], symbol: str) -> list[dict[str, Any]]:
    """解析 /kline/history 响应 → 标准 bar 列表（纯函数，供单测）

    records 为按 symbol 分组的二维数组；time 统一取 bob（bar 起点），
    与平台新浪源分钟 bar 的 time 语义一致。
    """
    data = payload.get("data") or {}
    records = data.get("records") or []
    out: list[dict[str, Any]] = []
    for group in records:
        if not isinstance(group, list):
            continue
        for bar in group:
            if not isinstance(bar, dict):
                continue
            sym = str(bar.get("s") or "").strip().lower()
            if sym and symbol and sym != symbol.strip().lower():
                continue
            try:
                if not float(bar.get("o") or 0) > 0 or not float(bar.get("c") or 0) > 0:
                    continue
            except (TypeError, ValueError):
                continue
            out.append(
                {
                    "time": _bar_time(bar),
                    "open": float(bar.get("o") or 0),
                    "high": float(bar.get("h") or 0),
                    "low": float(bar.get("l") or 0),
                    "close": float(bar.get("c") or 0),
                    "volume": int(float(bar.get("v") or 0)),
                    "settle": None,
                    "open_interest": None,
                }
            )
    out.sort(key=lambda b: b["time"])
    return [b for b in out if b["time"]]


def parse_current_response(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """解析 /kline/current 响应 → {symbol: 当根 bar}（含未收线 bar）

    data 为按 symbol 分组的二维数组，每组通常 1 根（最新 bar）；
    time 取 b（bar 起点，bob 语义），v 为当根增量成交量。
    供恢复态 forming 治愈：升级重启后用权威当根 OHLC 替换残缺聚合。
    """
    out: dict[str, dict[str, Any]] = {}
    for group in payload.get("data") or []:
        if not isinstance(group, list):
            continue
        for bar in group:
            if not isinstance(bar, dict):
                continue
            sym = str(bar.get("s") or "").strip().lower()
            try:
                if not sym or not float(bar.get("o") or 0) > 0:
                    continue
            except (TypeError, ValueError):
                continue
            time = str(bar.get("b") or "").strip()
            if not time:
                continue
            if len(time) == 16:  # 缺秒补齐
                time += ":00"
            out[sym] = {
                "time": time,
                "open": float(bar.get("o") or 0),
                "high": float(bar.get("h") or 0),
                "low": float(bar.get("l") or 0),
                "close": float(bar.get("c") or 0),
                "volume": int(float(bar.get("v") or 0)),
                "settle": None,
                "open_interest": None,
            }
    return out


def fetch_vvtr_current_bars(
    api_key: str,
    symbols: list[str],
    period: str,
    base_url: str = "",
) -> dict[str, dict[str, Any]]:
    """GET /kline/current → {symbol: 当根标准 bar}（支持批量，走全局 1 rps 节流）"""
    from app.connectors.vvtr.config import DEFAULT_BASE_URL, to_vvtr_symbol

    interval = _PERIOD_TO_INTERVAL.get(str(period or "").strip().lower())
    if not api_key or not interval or not symbols:
        return {}
    base = (base_url or DEFAULT_BASE_URL).strip()
    sym_arg = ",".join(to_vvtr_symbol(s) for s in symbols if str(s or "").strip())
    if not sym_arg:
        return {}
    payload = _request(
        base,
        "/kline/current",
        {"symbols": sym_arg, "interval": interval, "type": 14, "apiKey": api_key},
    )
    if not payload:
        return {}
    return parse_current_response(payload)


_PERIOD_MINUTES: dict[str, int] = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60,
}
_SUB15_INTERVALS = {"1m", "3m", "5m", "10m"}


def _lookback_days(interval: str, limit: int) -> int:
    """按目标根数推算回看天数（分钟线按 ~30% 交易占比放大 + 2 天缓冲）

    单页 2000 条：窗口过宽（1m 取 180 天 ≈ 27 万根）时翻页截断后
    拿到的是窗口头部旧数据，必须按 limit 收紧窗口保证拿到最新 K 线。
    窗口上限按官方规则分档：<15m 半年，15m~1d 两年，日线以上全历史。
    """
    if interval in _PERIOD_MINUTES:
        days = math.ceil(limit * _PERIOD_MINUTES[interval] / (0.3 * 1440)) + 2
        cap = _SUB15_WINDOW_DAYS if interval in _SUB15_INTERVALS else _MINUTE_WINDOW_DAYS
        return min(days, cap)
    # 1D/1W/1M：日线每日 1 根，交易日占比 ~65%
    days = math.ceil(limit / 0.65) + 10
    return min(days, _DAILY_WINDOW_DAYS)


def _fetch_history_window(
    api_key: str,
    base: str,
    vvtr_symbol: str,
    interval: str,
    from_str: str,
    to_str: str,
    limit: int,
) -> list[dict[str, Any]]:
    """拉一个 from/to 窗口的 K线（含 cursorToken 翻页，最多 3 页）"""
    is_daily = interval in ("1D", "1W", "1M")
    bars: list[dict[str, Any]] = []
    cursor: str | None = None
    for _page in range(3):
        params: dict[str, Any] = {
            "symbols": vvtr_symbol,
            "interval": interval,
            "type": 14,
            "from": from_str,
            "to": to_str,
            "apiKey": api_key,
        }
        if cursor:
            params["cursorToken"] = cursor
        payload = _request(base, "/kline/history", params)
        if not payload:
            break
        page_bars = parse_history_response(payload, vvtr_symbol)
        if is_daily:
            for bar in page_bars:
                bar["time"] = bar["time"][:10]
        bars.extend(page_bars)
        cursor = (payload.get("data") or {}).get("cursorToken") or None
        if not cursor or not (payload.get("data") or {}).get("hasMore"):
            break
        if len(bars) >= limit:
            break
    return bars


def fetch_vvtr_kline(
    api_key: str,
    symbol: str,
    period: str,
    limit: int = 500,
    base_url: str = "",
) -> list[dict[str, Any]]:
    """GET /kline/history → 最新 limit 根标准 bar（分钟三段式：日期历史 + 当日早晚两窗）

    实测服务端规则：纯日期格式支持多日/多月窗口但不含当日；完整时间
    格式仅适用于当日（跨日窗口返回 0 根）且长窗口翻页丢尾部。分钟线
    因此拆：历史段 from=N 天前 to=昨天（日期格式）、当日早段 00:00→
    12:00、当日近段 max(30min,3×周期)→+1h（时间格式短窗保尾部）。
    日线只需日期格式一段。
    """
    from app.connectors.vvtr.config import DEFAULT_BASE_URL, to_vvtr_symbol

    interval = _PERIOD_TO_INTERVAL.get(str(period or "").strip().lower())
    if not api_key or not interval:
        return []
    base = (base_url or DEFAULT_BASE_URL).strip()
    vvtr_symbol = to_vvtr_symbol(symbol)
    is_daily = interval in ("1D", "1W", "1M")
    now_bj = datetime.now(_BJ_TZ)
    from_dt = now_bj - timedelta(days=_lookback_days(interval, max(limit, 1)))

    if is_daily:
        bars = _fetch_history_window(
            api_key,
            base,
            vvtr_symbol,
            interval,
            from_dt.strftime("%Y-%m-%d"),
            now_bj.strftime("%Y-%m-%d"),
            limit,
        )
    else:
        hist = _fetch_history_window(
            api_key,
            base,
            vvtr_symbol,
            interval,
            from_dt.strftime("%Y-%m-%d"),
            (now_bj - timedelta(days=1)).strftime("%Y-%m-%d"),
            limit,
        )
        # 当日拆早晚两窗，且**窗口必须连续无缺口**：
        # - 早段 00:00 → min(12:00, now)：跨午夜夜盘尾 + 上午
        # - 近段 min(回看跨度, 今日12:00) → now+1h：下午/夜盘整段 + 最新已收 bar
        #   （近段起点取 min：下午起窗口自动扩到 12:00，否则夜里 21 点后
        #   下午 13:30-15:00 会掉在早段(→12:00)与近段(now-45min)之间的
        #   缝隙——2026-08-21 夜盘实测少 6 根 15m 即此缺口）
        # 当前未收 bar 由实时 forming 合并补齐，不依赖上游。
        d_today = now_bj.strftime("%Y-%m-%d")

        def _floor5(dt):
            discarded = (dt.minute % 5) * 60 + dt.second
            return dt - timedelta(seconds=discarded)

        def _ceil5(dt):
            padding = (5 - dt.minute % 5) * 60 - dt.second
            return dt + timedelta(seconds=padding if padding > 0 else 0)

        _noon = now_bj.replace(hour=12, minute=0, second=0, microsecond=0)
        am_to = _floor5(min(_noon, now_bj))
        am = _fetch_history_window(
            api_key,
            base,
            vvtr_symbol,
            interval,
            f"{d_today} 00:00:00",
            am_to.strftime("%Y-%m-%d %H:%M:%S"),
            limit,
        )
        _iv_min = {"1m": 1, "3m": 3, "5m": 5, "10m": 10, "15m": 15,
                   "30m": 30, "1h": 60}.get(interval, 15)
        recent_from = now_bj - timedelta(minutes=max(30, 3 * _iv_min))
        # 窗口边界按 5 分钟取整（from 向下、to 向上）：窗口恒覆盖所需范围，
        # 且中继缓存键在 5 分钟内稳定，避免分钟级轮转让近段窗口每分钟
        # 都打穿缓存直连上游（1 rps 节流下冷路径 ≈5s，切分时卡顿主因）。
        # pm_from ≤ am_to 保证两窗连续（缺口回归守卫见单测）。
        pm_from = _floor5(min(recent_from, _noon))
        pm = _fetch_history_window(
            api_key,
            base,
            vvtr_symbol,
            interval,
            pm_from.strftime("%Y-%m-%d %H:%M:%S"),
            _ceil5(now_bj + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"),
            limit,
        )
        bars = hist + am + pm

    # 去重（两段/翻页重叠）+ 截断
    seen: dict[str, dict[str, Any]] = {}
    for bar in bars:
        if bar["time"]:
            seen[bar["time"]] = bar
    ordered = [seen[k] for k in sorted(seen)]
    return ordered[-limit:] if limit > 0 else ordered
