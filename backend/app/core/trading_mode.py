"""交易盘模式 —— live 实盘数据 / virtual 虚拟盘（openctp 7×24）

会话级绑定：仅登录时选择，JWT 携带，业务查询强制带 mode。
"""

from __future__ import annotations

from typing import Final

MODE_LIVE: Final[str] = "live"
MODE_VIRTUAL: Final[str] = "virtual"
VALID_MODES: Final[frozenset[str]] = frozenset({MODE_LIVE, MODE_VIRTUAL})
DEFAULT_MODE: Final[str] = MODE_LIVE


def normalize_trading_mode(value: str | None) -> str:
    """非法值回退 live"""
    v = str(value or "").strip().lower()
    if v in VALID_MODES:
        return v
    return DEFAULT_MODE


def mode_label_zh(mode: str) -> str:
    """中文展示名"""
    m = normalize_trading_mode(mode)
    if m == MODE_VIRTUAL:
        return "虚拟盘"
    return "实盘"
