"""行情 Redis 键 —— 按交易盘隔离 live / virtual

live:  新浪 / SimNow（管理端渠道）
  - 新前缀 market:live:*
  - 兼容旧键 market:quote:*（双写/双读）
virtual: openctp 7×24
  - 仅 market:virtual:*
"""

from __future__ import annotations

import json
from typing import Any

from app.core.trading_mode import MODE_LIVE, MODE_VIRTUAL, normalize_trading_mode

LEGACY_QUOTE_PREFIX = "market:quote:"
LEGACY_ORDERBOOK_PREFIX = "market:orderbook:"
LEGACY_TRADES_PREFIX = "market:trades:"
LEGACY_CHANNEL_KEY = "market:channel"


def mode_ns(trading_mode: str | None) -> str:
    """模式命名空间片段"""
    m = normalize_trading_mode(trading_mode)
    return "virtual" if m == MODE_VIRTUAL else "live"


def is_virtual_mode(trading_mode: str | None) -> bool:
    return normalize_trading_mode(trading_mode) == MODE_VIRTUAL


def default_channel_for_mode(trading_mode: str | None) -> str:
    """各盘默认行情渠道"""
    if is_virtual_mode(trading_mode):
        return "openctp_724"
    return "sina"


def quote_key(trading_mode: str | None, symbol: str) -> str:
    """实时报价主键（mode 前缀）"""
    return f"market:{mode_ns(trading_mode)}:quote:{symbol.strip().lower()}"


def source_quote_key(source: str, symbol: str) -> str:
    """live 原始行情源快照；与下游统一 quote 隔离。"""
    src = source.strip().lower()
    sym = symbol.strip().lower()
    return f"market:live:source:{src}:quote:{sym}"


def source_quote_scan_pattern(source: str) -> str:
    return f"market:live:source:{source.strip().lower()}:quote:*"


def orderbook_key(trading_mode: str | None, symbol: str) -> str:
    return f"market:{mode_ns(trading_mode)}:orderbook:{symbol.strip().lower()}"


def trades_key(trading_mode: str | None, symbol: str) -> str:
    return f"market:{mode_ns(trading_mode)}:trades:{symbol.strip().lower()}"


def channel_key(trading_mode: str | None) -> str:
    """当前盘行情渠道控制键"""
    return f"market:{mode_ns(trading_mode)}:channel"


def ctp_front_key(trading_mode: str | None) -> str:
    return f"market:{mode_ns(trading_mode)}:ctp_md_front"


def quote_key_prefix(trading_mode: str | None) -> str:
    """bridge 写入前缀"""
    return f"market:{mode_ns(trading_mode)}:quote:"


def quote_scan_pattern(trading_mode: str | None) -> str:
    return f"market:{mode_ns(trading_mode)}:quote:*"


def orderbook_scan_pattern(trading_mode: str | None) -> str:
    return f"market:{mode_ns(trading_mode)}:orderbook:*"


def trades_scan_pattern(trading_mode: str | None) -> str:
    return f"market:{mode_ns(trading_mode)}:trades:*"


def legacy_live_quote_key(symbol: str) -> str:
    return f"{LEGACY_QUOTE_PREFIX}{symbol.strip().lower()}"


def write_keys_for_quote(trading_mode: str | None, symbol: str) -> list[str]:
    """写入 quote 时的全部键（live 双写旧键）"""
    sym = symbol.strip().lower()
    keys = [quote_key(trading_mode, sym)]
    if not is_virtual_mode(trading_mode):
        keys.append(legacy_live_quote_key(sym))
    return keys


def write_keys_for_orderbook(trading_mode: str | None, symbol: str) -> list[str]:
    sym = symbol.strip().lower()
    keys = [orderbook_key(trading_mode, sym)]
    if not is_virtual_mode(trading_mode):
        keys.append(f"{LEGACY_ORDERBOOK_PREFIX}{sym}")
    return keys


def write_keys_for_trades(trading_mode: str | None, symbol: str) -> list[str]:
    sym = symbol.strip().lower()
    keys = [trades_key(trading_mode, sym)]
    if not is_virtual_mode(trading_mode):
        keys.append(f"{LEGACY_TRADES_PREFIX}{sym}")
    return keys


def read_candidate_quote_keys(trading_mode: str | None, symbol: str) -> list[str]:
    """读 quote 候选键（优先 mode 前缀，live 回落旧键）"""
    sym = symbol.strip().lower()
    keys = [quote_key(trading_mode, sym)]
    if not is_virtual_mode(trading_mode):
        keys.append(legacy_live_quote_key(sym))
    return keys


async def get_quote_json(
    redis_conn: Any,
    trading_mode: str | None,
    symbol: str,
) -> dict[str, Any] | None:
    """按 mode 读取报价 JSON（live 兼容旧键）"""
    for key in read_candidate_quote_keys(trading_mode, symbol):
        try:
            raw = await redis_conn.get(key)
        except Exception:
            continue
        if not raw:
            continue
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="ignore")
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, TypeError):
            continue
    return None
