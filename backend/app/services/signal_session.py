"""信号播报的交易日窗口 —— 21:00 起至次日收盘"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any

from app.services.trading_hours import beijing_now

_BJ = timezone(timedelta(hours=8))


def session_window_start(now: datetime | None = None) -> datetime:
    """当前交易日数据窗口起点：最近一个 21:00（北京时间）。"""
    bj = (now or beijing_now()).astimezone(_BJ)
    today_21 = bj.replace(hour=21, minute=0, second=0, microsecond=0)
    if bj.time() >= time(21, 0):
        return today_21
    return today_21 - timedelta(days=1)


def parse_bar_dt(value: Any) -> datetime | None:
    """解析 bar 时间 → 北京时间。"""
    if value is None:
        return None
    text = str(value).strip().replace("T", " ").replace("Z", "")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            chunk = text[:19] if len(text) >= 19 else text
            dt = datetime.strptime(chunk, fmt)
            return dt.replace(tzinfo=_BJ)
        except ValueError:
            continue
    return None


def filter_session_bars(
    bars: list[dict[str, Any]],
    period: str,
    now: datetime | None = None,
    trading_mode: str | None = "live",
) -> list[dict[str, Any]]:
    """分钟线保留交易日窗口+缓冲；日线保留全部。

    virtual 7×24：不按国内夜盘窗口裁剪，保留完整序列。
    """
    if period == "1d" or not bars:
        return bars
    if str(trading_mode or "").lower() == "virtual":
        return bars
    win_start = session_window_start(now)
    pad_minutes = {
        "5m": 3 * 24 * 60,
        "15m": 3 * 24 * 60,
        "30m": 4 * 24 * 60,
        "60m": 5 * 24 * 60,
    }
    pad = timedelta(minutes=pad_minutes.get(period, 3 * 24 * 60))
    keep_from = win_start - pad
    kept: list[dict[str, Any]] = []
    for bar in bars:
        dt = parse_bar_dt(bar.get("time"))
        if dt is None or dt >= keep_from:
            kept.append(bar)
    return kept if len(kept) >= 35 else bars
