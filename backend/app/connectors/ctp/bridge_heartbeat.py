"""CTP bridge 心跳 —— 写入 Redis 供健康检查"""

from __future__ import annotations

import time
from typing import Any


def bridge_heartbeat_key(trading_mode: str) -> str:
    mode = "virtual" if trading_mode == "virtual" else "live"
    return f"market:{mode}:bridge:heartbeat"


def write_bridge_heartbeat(
    redis_client: Any,
    trading_mode: str,
    channel_label: str,
    min_interval_sec: float = 5.0,
    last_ts: list[float] | None = None,
) -> None:
    """节流写入心跳（last_ts 为长度 1 的可变列表，跨调用保留）"""
    if redis_client is None:
        return
    now = time.time()
    if last_ts is not None and last_ts and (now - last_ts[0]) < min_interval_sec:
        return
    if last_ts is not None:
        if not last_ts:
            last_ts.append(now)
        else:
            last_ts[0] = now
    payload = f"{int(now)}|{channel_label}|{trading_mode}"
    try:
        redis_client.set(bridge_heartbeat_key(trading_mode), payload, ex=120)
    except Exception:
        pass


def parse_heartbeat(raw: str | None) -> dict[str, Any]:
    """解析心跳字符串 → age_sec / ok"""
    if not raw:
        return {"ok": False, "age_sec": None, "channel": None}
    text = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else str(raw)
    parts = text.split("|")
    try:
        ts = float(parts[0])
    except (ValueError, IndexError):
        return {"ok": False, "age_sec": None, "channel": None}
    age = max(0.0, time.time() - ts)
    channel = parts[1] if len(parts) > 1 else None
    return {
        "ok": age <= 90.0,
        "age_sec": round(age, 1),
        "channel": channel,
        "ts": ts,
    }
