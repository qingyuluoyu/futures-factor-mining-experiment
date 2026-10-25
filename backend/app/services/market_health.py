"""行情健康检查 —— live / virtual 命名空间与渠道镜像"""

from __future__ import annotations

from typing import Any


async def _count_scan(redis_conn: Any, pattern: str, cap: int) -> int:
    """扫描匹配键数量，最多计到 cap（避免全库 SCAN 过重）"""
    n = 0
    async for _ in redis_conn.scan_iter(pattern):
        n += 1
        if n >= cap:
            break
    return n


async def _get_str(redis_conn: Any, key: str) -> str | None:
    try:
        raw = await redis_conn.get(key)
    except Exception:
        return None
    if raw is None:
        return None
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="ignore")
    return str(raw)


async def collect_market_health(
    redis_conn: Any,
    sample_symbol: str = "rb2610",
) -> dict[str, Any]:
    """汇总 live/virtual 行情健康（纯读 Redis）"""
    from app.services.market_keys import (
        LEGACY_CHANNEL_KEY,
        channel_key,
        get_quote_json,
        quote_scan_pattern,
    )

    sym = sample_symbol.strip().lower()
    live_n = await _count_scan(redis_conn, quote_scan_pattern("live"), 200)
    virt_n = await _count_scan(redis_conn, quote_scan_pattern("virtual"), 200)
    legacy_n = await _count_scan(redis_conn, "market:quote:*", 200)

    live_q = await get_quote_json(redis_conn, "live", sym)
    virt_q = await get_quote_json(redis_conn, "virtual", sym)

    live_ch = await _get_str(redis_conn, channel_key("live"))
    if live_ch is None:
        live_ch = await _get_str(redis_conn, LEGACY_CHANNEL_KEY)
    virt_ch = await _get_str(redis_conn, channel_key("virtual"))

    from app.connectors.ctp.bridge_heartbeat import (
        bridge_heartbeat_key,
        parse_heartbeat,
    )

    live_hb = parse_heartbeat(
        await _get_str(redis_conn, bridge_heartbeat_key("live"))
    )
    virt_hb = parse_heartbeat(
        await _get_str(redis_conn, bridge_heartbeat_key("virtual"))
    )

    live_ok = live_n > 0 or live_q is not None or legacy_n > 0
    virt_ok = virt_n > 0 or virt_q is not None

    active_ch = await _get_str(redis_conn, "market:live:active_channel")

    return {
        "sample_symbol": sym,
        "active_channel": active_ch or live_ch or "sina",
        "live": {
            "ok": live_ok,
            "channel": live_ch or "unknown",
            "quote_keys_sample": live_n,
            "legacy_quote_keys_sample": legacy_n,
            "sample_last_price": None
            if live_q is None
            else live_q.get("last_price"),
            "sample_source": None if live_q is None else live_q.get("source"),
            "bridge_heartbeat": live_hb,
        },
        "virtual": {
            "ok": virt_ok,
            "channel": virt_ch or "openctp_724",
            "quote_keys_sample": virt_n,
            "sample_last_price": None
            if virt_q is None
            else virt_q.get("last_price"),
            "sample_source": None if virt_q is None else virt_q.get("source"),
            "bridge_heartbeat": virt_hb,
            "hint": None
            if virt_ok
            else "无 virtual 行情：请启动 qihuo-ctp-bridge-virtual",
        },
        "ok": live_ok and virt_ok,
    }
