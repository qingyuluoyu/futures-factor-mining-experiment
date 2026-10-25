"""Real stock-router inclusion, default closure and market boundaries without services."""
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
import httpx
import pytest

from app.api.stock_factors import router
from app.core.deps import AuthContext, get_auth_context, get_current_user, get_session

BASE = "/api/stock/research/v2/models"


@pytest.mark.asyncio
async def test_stock_parent_mount_is_closed_by_default_without_registry_queries():
    app=FastAPI(); app.include_router(router)
    user=SimpleNamespace(id=uuid4(),market_type="stock")
    app.dependency_overrides[get_auth_context]=lambda:AuthContext(user,"virtual")
    app.dependency_overrides[get_session]=lambda:object()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
        response=await client.get(BASE+"/availability")
        assert response.status_code==200,response.text
        assert response.json()["listing_enabled"] is False and response.json()["training_enabled"] is False
        blocked=await client.get(BASE)
        assert blocked.status_code==503 and blocked.json()["detail"]["code"]=="ml_registry_disabled"
        assert (await client.post(BASE,json={"kind":"ml_train"})).status_code==405


@pytest.mark.asyncio
async def test_parent_stock_model_routes_never_reach_registry_for_futures_sessions():
    app=FastAPI(); app.include_router(router)
    app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(id=uuid4(),market_type="futures")
    async def forbidden():
        raise AssertionError("market must reject before model registry/database access")
    app.dependency_overrides[get_session]=forbidden
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
        for suffix in ["/availability","","/"+str(uuid4()),"/"+str(uuid4())+"/report"]:
            response=await client.get(BASE+suffix)
            assert response.status_code==409,response.text
            assert response.headers["x-market-capability"]=="stock_required"


def test_model_mount_adds_get_only_and_keeps_legacy_research_and_custom_paths():
    app=FastAPI(); app.include_router(router)
    paths={(path,method.upper()) for path,methods in app.openapi()["paths"].items() for method in methods}
    assert (BASE+"/availability","GET") in paths
    assert all(method=="GET" for path,method in paths if path.startswith(BASE))
    for entry in [("/api/stock/factors","GET"),("/api/stock/factors/analysis","POST"),("/api/stock/backtest","POST"),
            ("/api/stock/research/v2/custom-factors","GET"),("/api/stock/research/v2/custom-factors/validate","POST")]:
        assert entry in paths
