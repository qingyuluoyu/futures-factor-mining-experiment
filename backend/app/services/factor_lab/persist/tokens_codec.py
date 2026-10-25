"""因子 tokens 编解码纯函数 —— tokens_key / 安全整数列表"""

from __future__ import annotations

from typing import Any


def normalize_tokens(raw: Any) -> list[int]:
    """将任意输入规范为整数 token 列表；非法项丢弃"""
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for item in raw:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


def tokens_key(tokens: list[int]) -> str:
    """生成去重键（稳定、可索引）"""
    return ",".join(str(t) for t in tokens)


def truncate_text(text: str, max_len: int) -> str:
    """截断展示文本"""
    s = (text or "").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"
