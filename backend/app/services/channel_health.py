"""行情渠道健康检测 —— 判定单个渠道状态（green / orange / red）

CTP 渠道（openctp_724 / openctp_sim / real_ctp）：读 bridge 心跳 age，且心跳
channel_label 必须与被探测渠道一致（同一 mode 下只跑一个 bridge，靠心跳
payload 的 channel 字段区分当前在跑谁）。

sina：看 market:quote:{symbol} 的 trade_date 是否为今天（scheduler 每秒刷新）。

状态阈值：age <= 30s → green；30–90s → orange；> 90s 或无心跳 / channel
不匹配 → red。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.connectors.ctp.bridge_heartbeat import (
    bridge_heartbeat_key,
    parse_heartbeat,
)
from app.services.market_channel import (
    CHANNEL_OPENCTP_724,
    CHANNEL_SINA,
    CHANNEL_VVTR,
    is_ctp_channel,
    normalize_channel,
)

logger = logging.getLogger("qihuo.channel_health")

# 心跳 age 阈值（秒）
_AGE_GREEN = 30.0
_AGE_ORANGE = 90.0

_BJ_TZ = timezone(timedelta(hours=8))


def norm_trade_date(raw: Any) -> str:
    """归一化 trade_date 为 YYYY-MM-DD（兼容 sina 'YYYYMMDD' / bridge 'YYYY-MM-DD'）"""
    s = str(raw or "")
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def max_trade_date(quotes: list[dict]) -> str:
    """quote 列表中最大的归一化 trade_date（空列表返回 ''）"""
    dates = [
        norm_trade_date(q.get("trade_date"))
        for q in quotes
        if q.get("trade_date")
    ]
    return max(dates) if dates else ""


def _today_bj() -> str:
    """今天北京时间日期（YYYY-MM-DD）"""
    return datetime.now(_BJ_TZ).strftime("%Y-%m-%d")


async def _get_str(redis_conn: Any, key: str) -> str | None:
    """安全读 Redis string"""
    raw = await redis_conn.get(key)
    if raw is None:
        return None
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="ignore")
    return str(raw)


def _red(channel: str, reason: str) -> dict[str, Any]:
    """构造 red 状态结果"""
    return {
        "status": "red",
        "channel": channel,
        "age_sec": None,
        "last_price": None,
        "trade_date": None,
        "reason": reason,
    }


async def probe_channel_health(
    redis_conn: Any,
    channel: str,
    sample: str = "rb2610",
) -> dict[str, Any]:
    """探测单个渠道健康 → {status, channel, age_sec, last_price, trade_date}"""
    ch = normalize_channel(channel)
    sym = sample.strip().lower()

    # 取该渠道命名空间的样本行情（用于 last_price 展示）
    ns = "virtual" if ch == CHANNEL_OPENCTP_724 else "live"
    if ch == CHANNEL_SINA:
        sample_key = f"market:quote:{sym}"
    else:
        sample_key = f"market:{ns}:quote:{sym}"
    last_price: Any = None
    trade_date: str | None = None
    raw_q = await _get_str(redis_conn, sample_key)
    if raw_q:
        try:
            qd = json.loads(raw_q)
            last_price = qd.get("last_price")
            trade_date = norm_trade_date(qd.get("trade_date"))
        except (json.JSONDecodeError, TypeError):
            pass

    if ch == CHANNEL_SINA:
        # sina：桥心跳（qihuo-sina-bridge 容器写）+ trade_date 双重判定。
        # 心跳新鲜说明桥在轮询；trade_date=今天说明数据确实落到了统一键。
        import time as _time

        from app.connectors.sina.config import HEARTBEAT_KEY as SINA_HB_KEY

        hb_raw = await _get_str(redis_conn, SINA_HB_KEY)
        hb_age: float | None = None
        if hb_raw:
            try:
                hb_ts = float(hb_raw.split("|")[0])
                hb_age = max(0.0, _time.time() - hb_ts)
            except (ValueError, IndexError):
                hb_age = None
        if hb_age is None or hb_age > _AGE_ORANGE:
            reason = "sina-bridge 无心跳或心跳超时"
            out = _red(ch, reason)
            out["last_price"] = last_price
            out["trade_date"] = trade_date
            return out
        if trade_date == _today_bj():
            status = "green" if hb_age <= _AGE_GREEN else "orange"
        else:
            status = "orange"
        return {
            "status": status,
            "channel": ch,
            "age_sec": round(hb_age, 1),
            "last_price": last_price,
            "trade_date": trade_date,
        }

    if ch == CHANNEL_VVTR:
        # VVTR：读 ws_bridge 心跳（ts/channel/mode/connected/symbols）
        from app.connectors.vvtr.ws_bridge import VVTR_HEARTBEAT_KEY

        hb_raw = await _get_str(redis_conn, VVTR_HEARTBEAT_KEY)
        hb: dict[str, Any] = {}
        if hb_raw:
            try:
                hb = json.loads(hb_raw)
            except (json.JSONDecodeError, TypeError):
                hb = {}
        ts = hb.get("ts")
        age: float | None = None
        try:
            if ts is not None:
                import time as _time

                age = max(0.0, _time.time() - float(ts))
        except (TypeError, ValueError):
            age = None
        if age is None:
            return _red(ch, "无心跳（桥未启动或未启用）")
        if age > _AGE_ORANGE:
            return _red(ch, f"心跳超时 {age:.0f}s")
        status = "green" if age <= _AGE_GREEN else "orange"
        return {
            "status": status,
            "channel": ch,
            "age_sec": round(age, 1),
            "last_price": last_price,
            "trade_date": trade_date,
            "mode": hb.get("mode"),
            "connected": bool(hb.get("connected")),
            "symbols": hb.get("symbols"),
        }

    if not is_ctp_channel(ch):
        return _red(ch, "未知渠道")

    # CTP 渠道：bridge 心跳 age + channel_label 匹配
    mode = "virtual" if ch == CHANNEL_OPENCTP_724 else "live"
    hb = parse_heartbeat(await _get_str(redis_conn, bridge_heartbeat_key(mode)))
    age = hb.get("age_sec")
    hb_channel = hb.get("channel")
    # 同一 mode 下只有一个 bridge；心跳 channel 不匹配说明当前跑的不是本渠道
    if age is None or hb_channel != ch:
        return _red(ch, "无心跳或渠道不匹配")
    if age <= _AGE_GREEN:
        status = "green"
    elif age <= _AGE_ORANGE:
        status = "orange"
    else:
        status = "red"
    return {
        "status": status,
        "channel": ch,
        "age_sec": age,
        "last_price": last_price,
        "trade_date": trade_date,
    }


async def collect_channels_health(
    redis_conn: Any,
    channels: list[str],
    sample: str = "rb2610",
) -> dict[str, dict[str, Any]]:
    """探测多个渠道 → {channel: health}"""
    result: dict[str, dict[str, Any]] = {}
    for ch in channels:
        try:
            result[ch] = await probe_channel_health(redis_conn, ch, sample)
        except Exception:  # noqa: BLE001 — 单渠道失败不阻断其它
            logger.exception("探测渠道健康失败 channel=%s", ch)
            result[ch] = _red(ch, "探测异常")
    return result


def decide_active_channel(
    healths: dict[str, dict[str, Any]],
    primary: str,
    backup: str,
) -> str:
    """决定当前生效渠道。

    - 主 green/orange 均视为可用（orange=心跳 30–90s 抖动，不应来回切）
    - 主 red 且备 green/orange → 备
    - 否则保主
    """
    primary_status = healths.get(primary, {}).get("status")
    backup_status = healths.get(backup, {}).get("status")
    if primary_status in ("green", "orange"):
        return primary
    if backup_status in ("green", "orange"):
        return backup
    return primary
