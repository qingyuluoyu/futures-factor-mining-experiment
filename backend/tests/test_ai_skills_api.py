"""Skills API 路由测试

覆盖商城搜索、详情、Token 配置、认证校验等端点。
"""

import pytest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from httpx import AsyncClient, ASGITransport


def _get_test_app():
    """创建带 mock get_session 依赖的测试 app，解决 session_factory 缺失问题"""
    from app.main import app
    from app.core.deps import get_session

    async def _mock_session():
        session = AsyncMock()
        session.commit = AsyncMock()
        session.rollback = AsyncMock()
        session.delete = AsyncMock()
        session.flush = AsyncMock()
        session.add = AsyncMock()
        yield session

    app.dependency_overrides[get_session] = _mock_session
    return app


def _clear_overrides():
    """清理依赖覆盖"""
    from app.main import app
    app.dependency_overrides.clear()


# ============ 商城接口认证测试 ============


@pytest.mark.asyncio
async def test_marketplace_search_requires_auth():
    """商城搜索需要认证"""
    app = _get_test_app()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/ai/skills/marketplace/search?q=test")
            assert resp.status_code == 401
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_marketplace_leaderboard_requires_auth():
    """排行榜需要认证"""
    app = _get_test_app()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/ai/skills/marketplace/leaderboard")
            assert resp.status_code == 401
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_marketplace_detail_requires_auth():
    """详情需要认证"""
    app = _get_test_app()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/ai/skills/marketplace/nonexistent")
            assert resp.status_code == 401
    finally:
        _clear_overrides()


# ============ 配置端点认证测试 ============


@pytest.mark.asyncio
async def test_config_get_requires_auth():
    """获取配置需要认证"""
    app = _get_test_app()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/ai/skills/config")
            assert resp.status_code == 401
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_config_update_requires_auth():
    """更新配置需要认证"""
    app = _get_test_app()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put("/api/ai/skills/config", json={"token": "test"})
            assert resp.status_code == 401
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_config_delete_requires_auth():
    """删除配置需要认证"""
    app = _get_test_app()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete("/api/ai/skills/config")
            assert resp.status_code == 401
    finally:
        _clear_overrides()


# ============ 已安装接口认证测试 ============


@pytest.mark.asyncio
async def test_installed_list_requires_auth():
    """已安装列表需要认证"""
    app = _get_test_app()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/ai/skills/installed")
            assert resp.status_code == 401
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_install_requires_auth():
    """安装接口需要认证"""
    app = _get_test_app()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/ai/skills/install", json={})
            assert resp.status_code == 401
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_upload_requires_auth():
    """上传接口需要认证"""
    app = _get_test_app()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/ai/skills/upload")
            assert resp.status_code == 401
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_delete_requires_auth():
    """卸载接口需要认证"""
    app = _get_test_app()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(f"/api/ai/skills/installed/{uuid4()}")
            assert resp.status_code == 401
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_toggle_requires_auth():
    """切换接口需要认证"""
    app = _get_test_app()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(f"/api/ai/skills/installed/{uuid4()}/toggle")
            assert resp.status_code == 401
    finally:
        _clear_overrides()


# ============ 辅助函数测试 ============


def test_normalize_git_url_github_shorthand():
    """处理 github.com 短格式"""
    from app.api.ai_skills import _normalize_git_url
    result = _normalize_git_url("github.com/user/repo")
    assert result == "https://github.com/user/repo.git"


def test_normalize_git_url_install_prefix():
    """处理 /install 前缀"""
    from app.api.ai_skills import _normalize_git_url
    result = _normalize_git_url("/install github.com/user/repo")
    assert result == "https://github.com/user/repo.git"


def test_normalize_git_url_full():
    """完整 URL 自动补 .git"""
    from app.api.ai_skills import _normalize_git_url
    result = _normalize_git_url("https://github.com/user/repo")
    assert result == "https://github.com/user/repo.git"


def test_normalize_git_url_already_has_git():
    """已有 .git 后缀不再重复"""
    from app.api.ai_skills import _normalize_git_url
    result = _normalize_git_url("https://github.com/user/repo.git")
    assert result == "https://github.com/user/repo.git"
