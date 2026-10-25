"""K线多源交叉验证 —— 三源仲裁，≥2 源一致才视为权威数据

修正/补全 K线数据时，拉取多个备选源对比 OHLC：
- ≥2 源一致（容差 ≤ 1 个最小变动价位）→ 采纳共识值
- 3 源都不同 → TqSdk 仲裁（视为权威）
- 仅 1 源有数据 → 单源采纳（标记待验证）

容差用品种的最小变动价位（tick size），由 contracts.get_tick_size 提供。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("qihuo.kline_crosscheck")

# 交叉验证默认源顺序（tqsdk/eastmoney 已移除后仅剩新浪单源，
# 交叉验证退化为存在性校验，冲突由价格一致性门兜底）
DEFAULT_CHECK_SOURCES = ["sina"]

# 一致性容差倍数：≤ N 个 tick 算一致
_TOLERANCE_TICKS = 1


def _tolerance_for(code: str) -> float:
    """获取品种的一致性容差（1 个 tick size）"""
    from app.data.contracts import get_tick_size

    return get_tick_size(code) * _TOLERANCE_TICKS


def bars_consistent(
    b1: dict[str, Any],
    b2: dict[str, Any],
    tolerance: float,
) -> bool:
    """两根 bar 的 OHLC 是否在容差内一致

    对比 open/high/low/close 四个字段，每个字段差值 ≤ tolerance 算一致。
    volume 不对比（各源统计口径不同）。
    """
    for field in ("open", "high", "low", "close"):
        v1 = float(b1.get(field) or 0)
        v2 = float(b2.get(field) or 0)
        if abs(v1 - v2) > tolerance:
            return False
    return True


def _pick_consensus(
    bars_by_source: dict[str, dict[str, Any]],
    tolerance: float,
) -> tuple[dict[str, Any] | None, str]:
    """从多源同一 time 的 bar 中选出共识值

    返回 (consensus_bar, verdict):
    - verdict="verified": ≥2 源一致
    - verdict="single": 仅 1 源
    - verdict="conflict": 3 源都不同
    """
    sources = list(bars_by_source.keys())
    if not sources:
        return None, "skipped"
    if len(sources) == 1:
        return bars_by_source[sources[0]], "single"

    # 尝试找 ≥2 源一致的组合
    for i, s1 in enumerate(sources):
        for s2 in sources[i + 1 :]:
            if bars_consistent(bars_by_source[s1], bars_by_source[s2], tolerance):
                # 两源一致，取均值减少精度差异
                b1 = bars_by_source[s1]
                b2 = bars_by_source[s2]
                consensus = dict(b1)
                for field in ("open", "high", "low", "close"):
                    consensus[field] = round(
                        (float(b1.get(field) or 0) + float(b2.get(field) or 0)) / 2, 10
                    )
                # volume 取较大值
                consensus["volume"] = max(
                    int(b1.get("volume") or 0), int(b2.get("volume") or 0)
                )
                return consensus, "verified"

    # 所有源都不同
    # 仲裁：唯一剩余源
    for arb in ("sina",):
        if arb in bars_by_source:
            return bars_by_source[arb], "conflict"
    return bars_by_source[sources[0]], "conflict"


async def cross_validate_and_merge(
    symbol: str,
    period: str,
    sources: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """拉取多源数据，交叉验证，返回 (验证后的 bars, 统计)

    统计结构：
        {
          "verified": N,   # ≥2源一致
          "single": N,     # 仅1源有数据
          "conflict": N,   # 多源都不同，仲裁
          "skipped": 0,    # 无数据跳过
        }
    """
    from app.data.contracts import get_code_for_symbol
    from app.data.kline_provider import (
        _fetch_sina,
        _fetch_simnow,
        _valid_bars,
    )

    check_sources = sources or DEFAULT_CHECK_SOURCES
    code = get_code_for_symbol(symbol)
    tolerance = _tolerance_for(code) if code else 1.0

    # 并发拉取各源
    fetcher_map = {
        "sina": _fetch_sina,
        "simnow": _fetch_simnow,
    }
    async def _fetch_one(src: str) -> tuple[str, list[dict[str, Any]]]:
        fetcher = fetcher_map.get(src)
        if fetcher is None:
            return src, []
        try:
            loop = asyncio.get_event_loop()
            # 单源最多等 60 秒，避免某个源 hang 死整个交叉验证（如 eastmoney 偶发卡死）
            bars = await asyncio.wait_for(
                    loop.run_in_executor(None, fetcher, symbol, period),
                    timeout=60,
                )
            return src, _valid_bars(bars)
        except asyncio.TimeoutError:
            logger.warning(
                "交叉验证拉取 %s 超时(>60s) %s %s", src, symbol, period
            )
            return src, []
        except Exception:
            logger.debug("交叉验证拉取 %s 失败 %s", src, symbol, exc_info=True)
            return src, []

    results = await asyncio.gather(*[_fetch_one(s) for s in check_sources])

    # 按时间对齐：{time_key: {source: bar}}
    aligned: dict[str, dict[str, dict[str, Any]]] = {}
    for src, bars in results:
        for bar in bars:
            tkey = str(bar.get("time") or "")[:16]  # 日线取前10位，分钟取前16位
            if not tkey:
                continue
            aligned.setdefault(tkey, {})[src] = bar

    if not aligned:
        return [], {"verified": 0, "single": 0, "conflict": 0, "skipped": 0}

    # 逐时间点验证
    stats = {"verified": 0, "single": 0, "conflict": 0, "skipped": 0}
    merged: list[dict[str, Any]] = []
    for tkey in sorted(aligned.keys()):
        consensus, verdict = _pick_consensus(aligned[tkey], tolerance)
        if consensus is None:
            stats["skipped"] += 1
            continue
        stats[verdict] += 1
        merged.append(consensus)

    logger.info(
        "交叉验证 %s %s：%d 条（一致%d 单源%d 冲突%d 跳过%d，容差%s）",
        symbol, period, len(merged),
        stats["verified"], stats["single"], stats["conflict"], stats["skipped"],
        tolerance,
    )
    return _valid_bars(merged), stats
