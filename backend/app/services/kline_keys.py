"""K 线 Redis 键 —— live/virtual 分盘

- live 历史：kline:history:{symbol}:{period}（新浪）
- live 实时：kline:realtime:{symbol}:{period}
- virtual 历史：kline:virtual:history:{symbol}:{period}（openctp 收盘序列）
- virtual 实时：kline:virtual:realtime:{symbol}:{period}
"""

from __future__ import annotations

from app.core.trading_mode import MODE_VIRTUAL, normalize_trading_mode


def realtime_kline_key(
    trading_mode: str | None,
    symbol: str,
    period: str,
) -> str:
    """当前未收盘 bar 缓存键"""
    sym = symbol.strip().lower()
    if normalize_trading_mode(trading_mode) == MODE_VIRTUAL:
        return f"kline:virtual:realtime:{sym}:{period}"
    return f"kline:realtime:{sym}:{period}"


def history_kline_key(
    trading_mode: str | None,
    symbol: str,
    period: str,
) -> str:
    """已收盘历史序列键"""
    sym = symbol.strip().lower()
    if normalize_trading_mode(trading_mode) == MODE_VIRTUAL:
        return f"kline:virtual:history:{sym}:{period}"
    return f"kline:history:{sym}:{period}"


def realtime_kline_scan_pattern(trading_mode: str | None) -> str:
    """WS 扫描 forming K 线"""
    if normalize_trading_mode(trading_mode) == MODE_VIRTUAL:
        return "kline:virtual:realtime:*"
    return "kline:realtime:*"
