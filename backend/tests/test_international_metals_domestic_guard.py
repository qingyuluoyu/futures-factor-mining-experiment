from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.paper import EstimateRequest, PlaceOrderRequest
from app.schemas.ai_trading import CreateAITradingTaskRequest, EstimateCapitalRequest, UpdateAITradingTaskRequest
from app.schemas.backtest import BacktestRunRequest


@pytest.mark.parametrize("symbol", ["XAUUSD", "XAG/USD", "xau_usd.fxcm", "XAGUSD.FXCM", "XAUUSD.OANDA"])
@pytest.mark.parametrize("schema,payload", [
    (EstimateRequest, {"price": 4000, "quantity": 1}),
    (PlaceOrderRequest, {"direction": "buy", "offset": "open", "order_type": "limit", "price": 4000, "quantity": 1}),
    (EstimateCapitalRequest, {"qty": 1}),
    (UpdateAITradingTaskRequest, {}),
    (CreateAITradingTaskRequest, {"timeframe": "1d", "side_mode": "both", "position_mode": "fixed_qty"}),
    (BacktestRunRequest, {"strategy_type": "n_breakout", "timeframe": "1d", "start_date": "2026-07-01", "end_date": "2026-09-30"}),
])
def test_international_dollar_symbols_are_rejected_before_domestic_business(schema, payload, symbol):
    with pytest.raises(ValidationError, match="国际贵金属"):
        schema.model_validate({**payload, "symbol": symbol})


def test_domestic_products_keep_existing_multiplier_and_optional_updates():
    from app.services.paper.specs import product_spec_public, resolve_trade_params

    assert resolve_trade_params("au2612", None)["multiplier"] == 1000
    assert resolve_trade_params("ag2612", None)["multiplier"] == 15
    assert UpdateAITradingTaskRequest().symbol is None
    with pytest.raises(ValueError, match="国际贵金属"):
        resolve_trade_params("XAUUSD", None)
    with pytest.raises(ValueError, match="国际贵金属"):
        product_spec_public("XAGUSD.FXCM")


@pytest.mark.asyncio
async def test_product_specs_path_rejects_dollar_symbol_before_settings_access(caplog):
    import httpx
    from fastapi import FastAPI
    from types import SimpleNamespace
    from app.api.paper import router
    from app.core.deps import get_auth_context, get_session

    session = AsyncMock()
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_auth_context] = lambda: SimpleNamespace(user=SimpleNamespace(id=uuid4(), market_type="futures"), trading_mode="virtual")
    application.dependency_overrides[get_session] = lambda: session
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application, raise_app_exceptions=False), base_url="http://test") as client:
        response = await client.get("/api/paper/product-specs/XAUUSD.FXCM")
    assert response.status_code == 422
    assert "国际贵金属" in response.text
    assert len(response.headers.get("X-Request-ID", "")) == 32
    assert "error_type=RequestValidationError" in caplog.text
    assert not session.mock_calls


@pytest.mark.asyncio
async def test_manual_internal_order_rejects_dollar_metal_before_redis_or_database():
    from app.services.paper.trading_svc import place_order

    session, redis = AsyncMock(), AsyncMock()
    with pytest.raises(HTTPException) as error:
        await place_order(session, uuid4(), "XAUUSD", "buy", "open", "market", 4000, 1, "国际黄金", None, None, redis, "virtual")
    assert error.value.status_code == 422
    assert not session.mock_calls and not redis.mock_calls


@pytest.mark.asyncio
async def test_internal_research_and_ai_task_reject_dollar_metal_before_side_effects():
    from app.services.backtest.runner import run_backtest
    from app.services.ai_trading.task.task_svc import create_task

    session, redis = AsyncMock(), AsyncMock()
    with pytest.raises(ValueError, match="国际贵金属"):
        await run_backtest(session, redis, uuid4(), {"symbol": "XAGUSD.FXCM"})
    with pytest.raises(HTTPException) as error:
        await create_task(session, uuid4(), {"symbol": "XAU/USD"}, "virtual")
    assert error.value.status_code == 422
    assert not session.mock_calls and not redis.mock_calls
