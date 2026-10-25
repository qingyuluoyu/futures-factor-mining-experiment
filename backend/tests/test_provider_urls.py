"""渠道 base_url 智能拼接单测 —— 三种填法都得到正确端点

线上问题：OpenCode Zen GO 渠道把完整端点
https://opencode.ai/zen/go/v1/chat/completions 填进 base_url，
旧逻辑固定拼 /v1/chat/completions 得到 404。
"""

from __future__ import annotations

from app.services.ai.providers.urls import resolve_endpoint


GO_ROOT = "https://opencode.ai/zen/go"
GO_V1 = "https://opencode.ai/zen/go/v1"
GO_FULL = "https://opencode.ai/zen/go/v1/chat/completions"


def test_root_base_appends_v1() -> None:
    """填网关根路径：补 /v1/<endpoint>（原有行为）"""
    assert (
        resolve_endpoint(GO_ROOT, "/chat/completions")
        == "https://opencode.ai/zen/go/v1/chat/completions"
    )
    assert (
        resolve_endpoint("https://api.openai.com", "/models")
        == "https://api.openai.com/v1/models"
    )


def test_v1_base_does_not_duplicate() -> None:
    """填带 /v1 的基准地址（渠道文档常见）：不再重复补 /v1"""
    assert (
        resolve_endpoint(GO_V1, "/chat/completions")
        == "https://opencode.ai/zen/go/v1/chat/completions"
    )
    assert (
        resolve_endpoint("https://opencode.ai/zen/v1", "/models")
        == "https://opencode.ai/zen/v1/models"
    )


def test_full_endpoint_used_as_is() -> None:
    """直接粘贴完整端点：去重后原样使用"""
    assert (
        resolve_endpoint(GO_FULL, "/chat/completions")
        == "https://opencode.ai/zen/go/v1/chat/completions"
    )
    assert (
        resolve_endpoint("https://api.anthropic.com/v1/messages", "/messages")
        == "https://api.anthropic.com/v1/messages"
    )


def test_whitespace_and_trailing_slash_tolerated() -> None:
    assert (
        resolve_endpoint("  https://opencode.ai/zen/go/  ", "/chat/completions")
        == "https://opencode.ai/zen/go/v1/chat/completions"
    )


def test_empty_base_still_resolvable() -> None:
    assert resolve_endpoint("", "/models") == "/v1/models"
    assert resolve_endpoint(None, "/models") == "/v1/models"


def test_claude_and_embeddings_endpoints() -> None:
    assert (
        resolve_endpoint("https://api.anthropic.com", "/messages")
        == "https://api.anthropic.com/v1/messages"
    )
    assert (
        resolve_endpoint("https://api.openai.com/v1", "/embeddings")
        == "https://api.openai.com/v1/embeddings"
    )
