"""渠道 base_url 智能拼接 —— 兼容三种常见填法

用户在 AI 设置里可能填：
1. 域名/网关根：   https://opencode.ai/zen/go          → 补 /v1/<endpoint>
2. 带 /v1 的基准： https://opencode.ai/zen/go/v1       → 补 <endpoint>
3. 完整端点：      https://opencode.ai/zen/go/v1/chat/completions → 原样去重

历史上固定拼 "{base}/v1/{endpoint}"，填 2/3 两种时会得到
"/v1/v1/…" 或 "…/chat/completions/v1/chat/completions" 导致 404
（OpenCode Zen GO 渠道接入实例）。
"""

from __future__ import annotations


def resolve_endpoint(base_url: str | None, endpoint: str) -> str:
    """按 base_url 形态拼接完整端点 URL

    endpoint 形如 "/chat/completions"、"/messages"、"/models"、"/embeddings"。
    """
    base = (base_url or "").strip().rstrip("/")
    # 用户粘贴了完整端点：去掉尾部再按规则补齐（含 /v1 判定）
    if base.endswith(endpoint):
        base = base[: -len(endpoint)]
    if not base:
        return f"/v1{endpoint}"
    if base.endswith("/v1"):
        return f"{base}{endpoint}"
    return f"{base}/v1{endpoint}"
