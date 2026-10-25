"""TypeSafe Jev（System One 决策模型）渠道接入单测

- 预设渠道：api_type=jev，base_url 指向 api.typesafe.ai
- 目录探活：GET /v1/models，响应格式 {"models": [{"name": ...}]}（非 OpenAI 的 data[].id）
- 对话护栏：jev 无对话补全端点，工厂 / 决策客户端 / chat_once 必须给出明确错误
"""

from __future__ import annotations

import pytest

from app.api.ai_model import PRESET_CAPABILITIES
from app.api.ai_provider import PRESET_PROVIDERS
from app.schemas.ai_provider import ProviderCreate, ProviderUpdate
from app.services.ai.providers.factory import create_provider
from app.services.ai.providers.urls import resolve_endpoint

TYPESAFE_BASE = "https://api.typesafe.ai"


# ---------- 预设渠道 ----------

def test_preset_contains_typesafe_jev() -> None:
    jev = [p for p in PRESET_PROVIDERS if p.name == "TypeSafe Jev"]
    assert len(jev) == 1
    p = jev[0]
    assert p.api_type == "jev"
    assert p.base_url == TYPESAFE_BASE


def test_typesafe_base_resolves_models_endpoint() -> None:
    """base 填域名根：正确拼出 /v1/models；填到 /v1 也能识别"""
    assert resolve_endpoint(TYPESAFE_BASE, "/models") == f"{TYPESAFE_BASE}/v1/models"
    assert (
        resolve_endpoint(f"{TYPESAFE_BASE}/v1", "/models")
        == f"{TYPESAFE_BASE}/v1/models"
    )


# ---------- Schema 校验 ----------

def test_create_accepts_jev_type() -> None:
    body = ProviderCreate(
        name="TypeSafe Jev",
        api_type="jev",
        base_url=TYPESAFE_BASE,
        api_key="apikey_test",
    )
    assert body.api_type == "jev"


def test_update_accepts_jev_type() -> None:
    assert ProviderUpdate(api_type="jev").api_type == "jev"


@pytest.mark.parametrize("bad", ["jev1", "JEV", "systemone", ""])
def test_create_rejects_unknown_type(bad: str) -> None:
    with pytest.raises(Exception):
        ProviderCreate(
            name="x", api_type=bad, base_url=TYPESAFE_BASE, api_key="k"
        )


# ---------- 目录响应解析格式 ----------

def test_jev_catalog_format_is_models_name() -> None:
    """TypeSafe 目录端点返回 models[].name（实测），与 OpenAI data[].id 不同，
    探活分支须按 models[].name 解析。此处固化格式契约。"""
    sample = {
        "models": [
            {"name": "jev-latest", "description": "...", "release_date": "..."},
            {"name": "jev-preview", "description": "...", "release_date": "..."},
        ]
    }
    names = [
        str(item["name"])
        for item in sample.get("models", [])
        if isinstance(item, dict) and item.get("name")
    ]
    assert names == ["jev-latest", "jev-preview"]


# ---------- 模型能力表 ----------

def test_jev_model_capabilities() -> None:
    """Jev 是决策模型：不标 tool_calling（无对话端点），标 decision"""
    for mid in ("jev-latest", "jev-preview"):
        caps = PRESET_CAPABILITIES[mid]
        assert caps["capabilities"] == ["decision"]
        assert caps["is_multimodal"] is False


# ---------- 对话护栏 ----------

def test_factory_rejects_jev_chat() -> None:
    with pytest.raises(ValueError, match="决策模型"):
        create_provider("jev", TYPESAFE_BASE, "apikey_test", "jev-latest")


def test_factory_unknown_type_still_rejected() -> None:
    with pytest.raises(ValueError, match="不支持的接口规范"):
        create_provider("systemone", TYPESAFE_BASE, "k", "jev-latest")


# ---------- 端到端探活（真实出网，需环境变量提供测试 Key） ----------
#
# 本地/CI 未设置 TYPESAFE_TEST_KEY 时跳过；Key 不落库、不进 Git。
# 运行：TYPESAFE_TEST_KEY=apikey_xxx pytest tests/test_ai_provider_jev.py -k e2e


class _FakeProviderResult:
    def __init__(self, provider) -> None:
        self._provider = provider

    def scalar_one_or_none(self):
        return self._provider

    def first(self):
        return self._provider


class _FakeProviderSession:
    """只支撑 _get_user_provider 的单条等值 select"""

    def __init__(self, provider) -> None:
        self._provider = provider

    async def execute(self, stmt):
        return _FakeProviderResult(self._provider)


@pytest.mark.asyncio
async def test_e2e_provider_test_endpoint_jev(monkeypatch):
    """完整走 POST /api/ai/providers/{id}/test：真实调用 api.typesafe.ai/v1/models，
    验证 jev 分支的 URL 拼接、Bearer 认证与 models[].name 解析。"""
    import os
    import uuid

    api_key = os.environ.get("TYPESAFE_TEST_KEY")
    if not api_key:
        pytest.skip("未设置 TYPESAFE_TEST_KEY，跳过真实出网探活")

    from datetime import datetime, timezone
    from types import SimpleNamespace

    from app.core.deps import get_current_user, get_session
    from app.main import app
    from app.models.ai_provider import AIProvider
    from app.services.crypto import encrypt

    provider = SimpleNamespace(
        id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        name="TypeSafe Jev",
        api_type="jev",
        base_url="https://api.typesafe.ai",
        api_key=encrypt(api_key),
        is_active=True,
        thinking_enabled=None,
        created_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
    )

    async def _fake_session():
        yield _FakeProviderSession(provider)

    app.dependency_overrides[get_session] = _fake_session
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=provider.user_id
    )
    try:
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/api/ai/providers/{provider.id}/test")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok", body
    assert "jev-latest" in body["models"]
