"""新浪 HTTP API K 线数据源

分钟级接口：InnerFuturesNewService.getFewMinLine
日线接口：InnerFuturesNewService.getDailyKLine

策略：
- 1m：直接用新浪 1 分钟（约 1023 根）
- 5/15/30/60m：
  1) 用 1m 本地聚合覆盖「最近可覆盖」区间（OHLCV 准）
  2) 用新浪原生多分钟补更早历史（约 1023 根，open/volume 偶有失真）
  同时间键以 1m 聚合为准
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.data.product_specs import extract_product_code
from app.services.session_profiles import period_start_for_product

logger = logging.getLogger("qihuo.sina_kline")

_BASE_URL = "https://stock2.finance.sina.com.cn/futures/api/jsonp.php"

_PERIOD_MINUTES: dict[str, int] = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "60m": 60,
}

# 内部周期 → 新浪 type 参数
_SINA_TYPE: dict[str, str] = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "60m": "60",
}

SUPPORTED_PERIODS: list[str] = ["1m", "5m", "15m", "30m", "60m", "1d"]


def _parse_jsonp(text: str) -> list[dict[str, Any]]:
    """解析新浪 JSONP 响应，提取 JSON 数组"""
    start = text.find("([")
    end = text.rfind("])")
    if start == -1 or end == -1:
        return []
    json_str = text[start + 1 : end + 1]
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        logger.error("JSONP 响应解析失败: %s", text[:200])
        return []


def _normalize_record(rec: dict[str, Any]) -> dict[str, Any]:
    """标准化 K 线记录：新浪字段 → 内部格式"""
    settle_raw = rec.get("s")
    oi_raw = rec.get("p")
    time_raw = str(rec.get("d", "") or "").strip()
    # 分钟线统一到秒，便于与聚合结果对齐去重
    if len(time_raw) == 16 and " " in time_raw:
        time_raw = f"{time_raw}:00"
    return {
        "time": time_raw,
        "open": float(rec.get("o", 0)),
        "high": float(rec.get("h", 0)),
        "low": float(rec.get("l", 0)),
        "close": float(rec.get("c", 0)),
        "volume": int(float(rec.get("v", 0))),
        "settle": float(settle_raw) if settle_raw not in (None, "", "0") else None,
        "open_interest": int(float(oi_raw)) if oi_raw not in (None, "", "0") else None,
    }


def _parse_bar_dt(time_str: str) -> datetime | None:
    """解析 bar 时间串（北京时间 wall clock，无时区）"""
    t = str(time_str or "").strip().replace("T", " ")
    if not t:
        return None
    try:
        if len(t) >= 19:
            return datetime.strptime(t[:19], "%Y-%m-%d %H:%M:%S")
        if len(t) >= 16:
            return datetime.strptime(t[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return None


def _minute_label_to_start(symbol: str, end_label: str, minutes: int) -> str:
    """新浪分钟 bar 的【结束时刻标签】→ 统一的【起点时刻标签】。

    系统 K 线时间键统一为起点语义（与 VVTR 历史 b=bob、forming
    _get_period_start_time 一致）；新浪原生接口是结束口径。换算必须沿
    交易分钟轴（period_start_for_product）而不是墙钟减法——30m/60m 桶
    跨午休/小休时，墙钟跨度 ≠ 周期分钟数。
    """
    dt = _parse_bar_dt(end_label)
    if dt is None:
        return str(end_label or "")
    start: datetime | None = None
    if symbol:
        start = period_start_for_product(
            extract_product_code(symbol),
            dt - timedelta(seconds=1),
            minutes,
        )
    if start is None:
        # 无品种交易轴信息：回退墙钟取整（1m 恒正确，多分钟仅近似的兜底）。
        # 结束标签减 1 秒再取整：恰好落在桶边界的结束标签（如 09:30 结束
        # 的 30m 桶）才能正确回退到 09:00 起点。
        probe = dt - timedelta(seconds=1)
        total = probe.hour * 60 + probe.minute
        start_total = (total // minutes) * minutes if minutes > 0 else total
        base = probe.replace(hour=0, minute=0, second=0, microsecond=0)
        start = base + timedelta(minutes=start_total)
    return start.strftime("%Y-%m-%d %H:%M:%S")


def _ceil_period_end(dt: datetime, minutes: int) -> datetime:
    """将 1 分钟 bar 的结束时刻上取整到周期结束时刻"""
    total = dt.hour * 60 + dt.minute
    if minutes <= 0:
        return dt.replace(second=0, microsecond=0)
    if total % minutes == 0:
        end_total = total
    else:
        end_total = (total // minutes + 1) * minutes
    base = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return base + timedelta(minutes=end_total)


def aggregate_from_1m(
    bars_1m: list[dict[str, Any]],
    period: str,
    symbol: str = "",
) -> list[dict[str, Any]]:
    """由 1 分钟 K 线聚合多分钟 K 线（时间键 = 周期【起点】时刻）

    输入约定：bars_1m 的时间键为分钟起点（系统统一语义；新浪适配层
    已在取数时换算，PG 新写入的 forming/vvtr 行亦为起点）。历史遗留
    的结束标签行会整体错桶一个周期，由 PG 标签归一脚本清理。
    """
    minutes = _PERIOD_MINUTES.get(period)
    if minutes is None:
        return []
    if minutes == 1:
        return list(bars_1m)

    buckets: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for bar in bars_1m:
        dt = _parse_bar_dt(str(bar.get("time") or ""))
        if dt is None:
            continue
        o = float(bar.get("open") or 0)
        h = float(bar.get("high") or 0)
        l = float(bar.get("low") or 0)
        c = float(bar.get("close") or 0)
        if not (o > 0 and h > 0 and l > 0 and c > 0):
            continue
        start = None
        if symbol:
            # 按品种交易分钟轴把 1m 起点映射到多分钟桶起点（跳过小休/午休）
            start = period_start_for_product(
                extract_product_code(symbol),
                dt,
                minutes,
            )
        if start is None:
            total = dt.hour * 60 + dt.minute
            start_total = (total // minutes) * minutes
            base = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            start = base + timedelta(minutes=start_total)
        key = start.strftime("%Y-%m-%d %H:%M:%S")
        vol = int(bar.get("volume") or 0)
        oi = bar.get("open_interest")
        settle = bar.get("settle")
        if key not in buckets:
            buckets[key] = {
                "time": key,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": vol,
                "settle": settle,
                "open_interest": oi,
            }
            continue
        b = buckets[key]
        b["high"] = max(float(b["high"]), h)
        b["low"] = min(float(b["low"]), l)
        b["close"] = c
        b["volume"] = int(b["volume"] or 0) + vol
        if oi is not None:
            b["open_interest"] = oi
        if settle is not None:
            b["settle"] = settle
    return list(buckets.values())


def _drop_incomplete_first_bucket(
    bars_1m: list[dict[str, Any]],
    aggregated: list[dict[str, Any]],
    period: str,
    symbol: str,
) -> list[dict[str, Any]]:
    """只保留能证明从桶起点开始的首个 1m 聚合桶。

    新浪 1m 接口是固定长度窗口，首条经常落在多分钟桶中段。这样的残缺桶
    不能覆盖原生完整 OHLCV；后续桶仍可作为精确近端数据。
    1m 时间键为起点语义：首条 1m 的起点 == 其所属多分钟桶起点 才算完整。
    """
    minutes = _PERIOD_MINUTES.get(period)
    if not bars_1m or not aggregated or minutes is None or minutes <= 1:
        return aggregated
    first_dt: datetime | None = None
    for bar in bars_1m:
        candidate = _parse_bar_dt(str(bar.get("time") or ""))
        prices = [
            float(bar.get(key) or 0)
            for key in ("open", "high", "low", "close")
        ]
        if candidate is not None and all(price > 0 for price in prices):
            first_dt = candidate
            break
    if first_dt is None:
        return aggregated[1:]
    expected_start: datetime | None = None
    if symbol:
        expected_start = period_start_for_product(
            extract_product_code(symbol), first_dt, minutes
        )
    if expected_start is None:
        total = first_dt.hour * 60 + first_dt.minute
        start_total = (total // minutes) * minutes
        base = first_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        expected_start = base + timedelta(minutes=start_total)
    if first_dt != expected_start:
        return aggregated[1:]
    return aggregated


def merge_history_with_accurate(
    history: list[dict[str, Any]],
    accurate: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """合并长历史与高精度近期：同 time 以 accurate 覆盖"""
    by_time: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for bar in history:
        t = str(bar.get("time") or "")
        if not t:
            continue
        by_time[t] = bar
    for bar in accurate:
        t = str(bar.get("time") or "")
        if not t:
            continue
        by_time[t] = bar
    return sorted(by_time.values(), key=lambda b: str(b.get("time") or ""))


def _fetch_sina_minute_raw(symbol: str, sina_type: str) -> list[dict[str, Any]]:
    """从新浪拉取指定 type 的分钟 K 线（时间标签换算为统一的起点语义）"""
    url = f"{_BASE_URL}/=/InnerFuturesNewService.getFewMinLine"
    params = {
        "symbol": symbol.lower(),
        "type": sina_type,
    }
    resp = httpx.get(url, params=params, timeout=15)
    resp.raise_for_status()
    records = _parse_jsonp(resp.text)
    bars = [_normalize_record(r) for r in records]
    # 新浪原生是【结束时刻】标签；系统统一起点语义（VVTR/ forming 同轴）。
    # 必须沿交易分钟轴换算，跨午休的长周期桶墙钟减法会错桶。
    minutes = int(sina_type) if str(sina_type).isdigit() else 0
    if minutes > 0:
        for bar in bars:
            t = str(bar.get("time") or "")
            if len(t) >= 16:  # 仅分钟 bar（日线 date-only 不动）
                bar["time"] = _minute_label_to_start(symbol, t, minutes)
    # 掐掉未来脏行：新浪收盘后/周末会在尾部返回下一交易日的垃圾 bar
    # （实测 2026-08-22：au2612 1m 尾行标签为次日 00:00、15m 为次日
    # 23:45），未来行会污染聚合桶、触发价格门误拒整个源、并顶到序列
    # 末尾把图表画到未来。超过 当前时间+2 分钟 一律丢弃。
    _bj = timezone(timedelta(hours=8))
    horizon = datetime.now(_bj).replace(tzinfo=None) + timedelta(minutes=2)
    out: list[dict[str, Any]] = []
    for bar in bars:
        dt = _parse_bar_dt(str(bar.get("time") or ""))
        if dt is not None and dt > horizon:
            logger.warning(
                "新浪分钟脏行已丢弃 %s %s: %s",
                symbol, sina_type, bar.get("time"),
            )
            continue
        out.append(bar)
    return out


def fetch_minute_kline_strict(symbol: str, period: str) -> list[dict[str, Any]]:
    """同 fetch_minute_kline，但上游异常直接抛出。

    sina 桥守卫（connectors/sina/guard.py）用：干净空结果=合约无数据
    （不计故障），异常=上游故障（计入熔断）。行为与 fetch_minute_kline
    完全同轴，公开函数内部委托本实现。
    """
    if period not in _PERIOD_MINUTES:
        raise ValueError(f"不支持的分钟周期: {period}")

    if period == "1m":
        return _fetch_sina_minute_raw(symbol, "1")

    # 所有多分钟周期都用交易时段感知的 1m 聚合。
    # 聚合按实际成交分钟累计，跳过商品小休和午休，30m/60m 不再错位。
    bars_1m = _fetch_sina_minute_raw(symbol, "1")
    accurate = aggregate_from_1m(bars_1m, period, symbol) if bars_1m else []
    return _drop_incomplete_first_bucket(bars_1m, accurate, period, symbol)


def fetch_minute_kline(symbol: str, period: str) -> list[dict[str, Any]]:
    """获取分钟级 K 线数据（失败吞异常返回 []，运维脚本兼容语义）

    - 1m：新浪 1 分钟接口
    - 5m/15m/30m/60m：交易时段感知的 1m 本地聚合（与 VVTR/交易所同轴）

    新浪原生多分钟接口（getFewMinLine type=5/15/30/60）已停用
    （2026-08-22，用户指令）：其结束标签口径需换算、且收盘后/周末
    会返回未来日期的垃圾行（实测 au2612 尾行次日 23:45），多源混画
    时 K 线排列错位。更早的多分钟历史由 PG（simnow 源）/remote 兜底。
    """
    try:
        return fetch_minute_kline_strict(symbol, period)
    except Exception:
        logger.error(
            "获取分钟 K 线失败: symbol=%s period=%s",
            symbol,
            period,
            exc_info=True,
        )
        return []


def fetch_daily_kline_strict(symbol: str) -> list[dict[str, Any]]:
    """同 fetch_daily_kline，但上游异常直接抛出（桥守卫用）"""
    sym_upper = symbol.upper()
    path = f"/var%20_{sym_upper}2021_08_17=/InnerFuturesNewService.getDailyKLine"
    url = f"{_BASE_URL}{path}"
    params = {
        "symbol": symbol.lower(),
        "_": "2021_08_17",
    }

    resp = httpx.get(url, params=params, timeout=10)
    resp.raise_for_status()
    records = _parse_jsonp(resp.text)
    return [_normalize_record(r) for r in records]


def fetch_daily_kline(symbol: str) -> list[dict[str, Any]]:
    """日线 K 线数据（失败吞异常返回 []，运维脚本兼容语义）"""
    try:
        return fetch_daily_kline_strict(symbol)
    except Exception:
        logger.error("获取日线 K 线失败: symbol=%s", symbol, exc_info=True)
        return []
