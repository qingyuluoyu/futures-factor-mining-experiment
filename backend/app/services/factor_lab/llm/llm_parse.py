"""路线 B：解析 LLM 候选 tokens"""

from __future__ import annotations

from typing import Any

from app.services.factor_lab.ops import OPS_CONFIG
from app.services.factor_lab.persist.tokens_codec import normalize_tokens
from app.services.factor_lab.vm import FEAT_OFFSET


def max_token_id() -> int:
    """合法 token 上界（特征 + 算子）"""
    return FEAT_OFFSET + len(OPS_CONFIG) - 1


def parse_candidates(data: dict[str, Any] | None) -> list[list[int]]:
    """从 JSON 提取 candidates[].tokens，过滤非法"""
    if not isinstance(data, dict):
        return []
    raw_list = data.get("candidates")
    if not isinstance(raw_list, list):
        return []
    hi = max_token_id()
    out: list[list[int]] = []
    seen: set[tuple[int, ...]] = set()
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        toks = normalize_tokens(item.get("tokens"))
        if not toks:
            continue
        if any(t < 0 or t > hi for t in toks):
            continue
        if len(toks) > 32:
            continue
        key = tuple(toks)
        if key in seen:
            continue
        seen.add(key)
        out.append(toks)
    return out
