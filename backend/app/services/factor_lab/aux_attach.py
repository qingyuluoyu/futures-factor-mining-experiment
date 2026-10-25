"""扩展信息源挂载(服务端)—— 桌面端扩展 token 公式的 bars 预处理

桌面端本地挖掘的公式可能含扩展信息源特征(id 40-58,aux_sources.py):
bar 上没有 x_* 字段时 feature_matrix 只出 BASE_FEAT_COUNT 行,含这些
token 的公式无信号(invalid_factor)。本模块在服务端执行这类公式前给
bars 挂上信息源,口径与桌面端 attachAux 同源——本品种 + 同板块伙伴的
品种日线 → sector_by_day → attach_sector,aux_sources 纯函数两端逐字
一致,数值同源由代码同构保证。

服务端能力边界:仅 sec 组(板块动量,id 45-48)。桌面端产品化同样只开
sec(仅日线挖掘);ts/dtr/ids 组无服务端数据管道,含其 token 的公式保持
无信号(桌面端 UI 也挖不出这三组,实际不会出现)。

容错:实盘路径不允许因信息源缺料而中断任务评估——挂载失败时返回原
bars(公式自然退化为无信号),记 warning 日志。回测路径同理(上层会以
「因子公式无效或无法执行」如实报错)。
"""

from __future__ import annotations

import time
from typing import Any

from .features import BASE_FEAT_COUNT
from .features.aux_sources import AUX_FEATURES, attach_sector, sector_by_day

# 服务端有数据管道的信息源组;其余组(ts/dtr/ids)token 合法但算不出
SUPPORTED_AUX_GROUPS: frozenset[str] = frozenset({"sec"})

# 日线缓存 TTL:实盘每根新 bar 评估一次,5 分钟内复用(与桌面端 DAILY_TTL 同值)
_DAILY_TTL_S = 5 * 60
_daily_cache: dict[tuple[str, str, int], tuple[float, Any]] = {}


def collect_factor_tokens(obj: Any, out: list[int] | None = None) -> list[int]:
    """递归收集对象里所有 factor_tokens(策略参数/量化子策略/组合公式),拍平"""
    if out is None:
        out = []
    if isinstance(obj, (list, tuple)):
        for v in obj:
            collect_factor_tokens(v, out)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k == "factor_tokens":
                _flatten_numbers(v, out)
            else:
                collect_factor_tokens(v, out)
    return out


def _flatten_numbers(v: Any, out: list[int]) -> None:
    if isinstance(v, (list, tuple)):
        for x in v:
            _flatten_numbers(x, out)
    else:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return
        if f.is_integer():
            out.append(int(f))


def tokens_aux_groups(tokens: list[int]) -> set[str]:
    """公式(拍平后的 token 列表)用到的扩展信息源组"""
    out: set[str] = set()
    base = BASE_FEAT_COUNT  # AUX_FEATURES 从该 id 起(append-only 冻结)
    for t in tokens:
        idx = int(t) - base
        if 0 <= idx < len(AUX_FEATURES):
            out.add(AUX_FEATURES[idx][2])
    return out


def tokens_need_attach(tokens: list[int]) -> bool:
    """tokens 是否用到服务端可挂载的信息源组(sec)"""
    return bool(tokens_aux_groups(tokens) & SUPPORTED_AUX_GROUPS)


def tokens_unsupported_groups(tokens: list[int]) -> set[str]:
    """tokens 用到但服务端无数据管道的组(诊断用)"""
    return tokens_aux_groups(tokens) - SUPPORTED_AUX_GROUPS


async def _cached_daily(session, code: str, end_date: str, limit: int) -> list[dict[str, Any]]:
    key = (code, end_date, limit)
    hit = _daily_cache.get(key)
    if hit is not None and time.monotonic() - hit[0] < _DAILY_TTL_S:
        return hit[1]
    from app.services.kline_history import query_history_bars

    bars = await query_history_bars(session, code.upper(), end_date, limit)
    _daily_cache[key] = (time.monotonic(), bars)
    return bars


async def attach_aux_for_tokens(
    session,
    symbol: str,
    timeframe: str,
    bars: list[dict[str, Any]],
    tokens: list[int],
) -> list[dict[str, Any]]:
    """给 bars 挂 tokens 所需(且服务端支持)的扩展信息源;不需要时原样返回

    失败不抛(调用方为实盘评估热路径):任何异常由调用方兜底或上层按
    「因子无效」处理。返回新列表,不改动入参 bars。
    """
    import logging

    log = logging.getLogger(__name__)
    groups = tokens_aux_groups(tokens) & SUPPORTED_AUX_GROUPS
    if not groups or not bars:
        return bars
    try:
        from app.data.contracts import get_code_for_symbol
        from app.data.product_sectors import get_cross_peers

        code = get_code_for_symbol(symbol) or (
            "".join(ch for ch in str(symbol).lower() if ch.isalpha())
        )
        from datetime import datetime, timedelta

        end = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        # 板块 20 日收益/均线需要段首之前约 45 天预热(与桌面端 shiftDays(-45) 同源)
        limit = max(240, len(bars) + 60)
        own_daily = await _cached_daily(session, code, end, limit)
        peers: dict[str, list[dict[str, Any]]] = {}
        for peer in get_cross_peers(code):
            pb = await _cached_daily(session, peer, end, limit)
            if pb and len(pb) >= 30:
                peers[peer] = pb
        if not own_daily or not peers:
            log.warning(
                "aux_attach: %s 板块动量材料不足(own=%d peers=%d),因子退化为无信号",
                symbol,
                len(own_daily),
                len(peers),
            )
            return bars
        sec_by_day = sector_by_day(own_daily, peers)
        return attach_sector(bars, sec_by_day)
    except Exception:
        log.warning("aux_attach: %s 挂载扩展信息源失败", symbol, exc_info=True)
        return bars
