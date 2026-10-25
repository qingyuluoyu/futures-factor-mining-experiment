"""MiniMax 渠道 + 思考模式开关单测

MiniMax 走 Anthropic 兼容接口（api_type=claude），
其兼容层 thinking 仅支持 {"type":"adaptive"} 开启、{"type":"disabled"} 关闭，
不支持 Anthropic 标准的 enabled/budget_tokens。
渠道级开关 thinking_enabled 三态：None=跟随模型自动 / True=强制开 / False=强制关。
"""

from __future__ import annotations

from app.api.ai_provider import PRESET_PROVIDERS, MINIMAX_MODEL_CATALOG, _is_minimax_url
from app.api.ai_model import PRESET_CAPABILITIES
from app.schemas.ai_provider import ProviderUpdate
from app.services.ai.providers.claude_provider import ClaudeProvider
from app.services.ai.providers.factory import create_provider
from app.services.ai.providers.urls import resolve_endpoint

ANTHROPIC_BASE = "https://api.anthropic.com"
MINIMAX_BASE = "https://api.minimax.cn/anthropic"


def _claude(model_id: str, thinking: bool | None, base_url: str = ANTHROPIC_BASE) -> ClaudeProvider:
    return ClaudeProvider(base_url, "sk-test", model_id, thinking_enabled=thinking)


# ---------- build_request：思考三态 ----------

def test_auto_mode_thinking_model_still_enabled() -> None:
    """自动（None）+ Claude 思考模型：保持原行为 enabled+budget"""
    body = _claude("claude-sonnet-4-20250514", None).build_request([{"role": "user", "content": "hi"}])
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 10000}


def test_auto_mode_minimax_m3_default_off() -> None:
    """自动（None）+ MiniMax M3：不在思考白名单，不发 thinking（官方默认关）"""
    body = _claude("MiniMax-M3", None, MINIMAX_BASE).build_request([{"role": "user", "content": "hi"}])
    assert "thinking" not in body


def test_force_on_minimax_uses_adaptive() -> None:
    """强制开 + MiniMax 官方域：兼容层只认 adaptive"""
    body = _claude("MiniMax-M3", True, MINIMAX_BASE).build_request([{"role": "user", "content": "hi"}])
    assert body["thinking"] == {"type": "adaptive"}


def test_force_on_minimax_by_model_prefix() -> None:
    """强制开 + 非官方域自定义渠道：按模型名前缀识别 MiniMax"""
    body = _claude("MiniMax-M2.7", True, "https://my-gateway.example.com").build_request(
        [{"role": "user", "content": "hi"}]
    )
    assert body["thinking"] == {"type": "adaptive"}


def test_force_on_anthropic_uses_enabled_budget() -> None:
    """强制开 + Anthropic 官方域：标准 enabled+budget_tokens"""
    body = _claude("claude-3-5-sonnet-20241022", True).build_request(
        [{"role": "user", "content": "hi"}]
    )
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 10000}


def test_force_off_disables_thinking_model() -> None:
    """强制关 + 原本会自动开启的思考模型：不发 thinking 参数"""
    body = _claude("claude-sonnet-4-20250514", False).build_request(
        [{"role": "user", "content": "hi"}]
    )
    assert "thinking" not in body


def test_force_off_minimax() -> None:
    """强制关 + MiniMax：不发 thinking（M3 关；M2 常开由服务端决定）"""
    body = _claude("MiniMax-M3", False, MINIMAX_BASE).build_request(
        [{"role": "user", "content": "hi"}]
    )
    assert "thinking" not in body


# ---------- 工厂透传 ----------

def test_factory_passes_thinking_enabled() -> None:
    provider = create_provider("claude", ANTHROPIC_BASE, "sk-test", "claude-sonnet-4-20250514", True)
    assert isinstance(provider, ClaudeProvider)
    assert provider.thinking_enabled is True


def test_factory_default_thinking_none() -> None:
    provider = create_provider("claude", ANTHROPIC_BASE, "sk-test", "claude-sonnet-4-20250514")
    assert provider.thinking_enabled is None


# ---------- MiniMax 预设与模型目录 ----------

def test_preset_contains_minimax() -> None:
    minimax = [p for p in PRESET_PROVIDERS if p.name == "MiniMax"]
    assert len(minimax) == 1
    p = minimax[0]
    assert p.api_type == "claude"
    assert p.base_url == "https://api.minimax.cn/anthropic"


def test_minimax_base_resolves_messages_endpoint() -> None:
    """base 填官方 /anthropic 路径：正确拼出 /anthropic/v1/messages 与 /models"""
    assert (
        resolve_endpoint(MINIMAX_BASE, "/messages")
        == "https://api.minimax.cn/anthropic/v1/messages"
    )
    assert (
        resolve_endpoint(MINIMAX_BASE, "/models") == "https://api.minimax.cn/anthropic/v1/models"
    )


def test_minimax_url_detection() -> None:
    assert _is_minimax_url("https://api.minimax.cn/anthropic")
    assert _is_minimax_url("https://api.minimaxi.com/anthropic")  # 海外域
    assert not _is_minimax_url("https://api.anthropic.com")
    assert not _is_minimax_url("https://opencode.ai/zen/go")


def test_minimax_model_catalog_and_capabilities() -> None:
    """测试回退目录与模型能力表覆盖 MiniMax 全系列"""
    assert "MiniMax-M3" in MINIMAX_MODEL_CATALOG
    assert len(MINIMAX_MODEL_CATALOG) == 8
    m3 = PRESET_CAPABILITIES["MiniMax-M3"]
    assert "thinking" in m3["capabilities"] and m3["is_multimodal"] is True
    for m in ("MiniMax-M2.7", "MiniMax-M2.5-highspeed", "MiniMax-M2"):
        assert "thinking" in PRESET_CAPABILITIES[m]["capabilities"]


# ---------- Schema：显式 null 恢复自动 ----------

def test_update_explicit_null_vs_omitted() -> None:
    """显式传 null（恢复自动）与省略字段（不改动）必须可区分"""
    explicit = ProviderUpdate(thinking_enabled=None)
    omitted = ProviderUpdate()
    assert "thinking_enabled" in explicit.model_fields_set
    assert "thinking_enabled" not in omitted.model_fields_set


def test_update_accepts_bool_values() -> None:
    assert ProviderUpdate(thinking_enabled=True).thinking_enabled is True
    assert ProviderUpdate(thinking_enabled=False).thinking_enabled is False
