import logging

import httpx
import pytest
from fastapi import APIRouter, FastAPI, HTTPException, Response
from pydantic import BaseModel

from app.core.financial_request_route import FinancialRequestRoute


class ValidatedInput(BaseModel):
    quantity: int


def application():
    router = APIRouter(route_class=FinancialRequestRoute)

    @router.post("/validate")
    async def validate(body: ValidatedInput):
        return {"quantity": body.quantity}

    @router.get("/existing")
    async def existing(response: Response):
        response.headers["X-Request-ID"] = "business-trace"
        return {"ok": True}

    @router.get("/denied")
    async def denied():
        raise HTTPException(401, "authentication required", headers={"WWW-Authenticate": "Bearer", "X-Request-ID": "business-denied-trace"})

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.mark.asyncio
async def test_validation_tracing_preserves_contract_without_logging_input(caplog):
    caplog.set_level(logging.INFO, logger="qihuo.financial_request")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application()), base_url="http://test") as client:
        result = await client.post("/validate", json={"quantity": "secret-not-a-number"}, headers={"X-Request-ID": "untrusted-id"})
    assert result.status_code == 422
    assert result.json()["detail"][0]["loc"] == ["body", "quantity"]
    assert len(result.headers["X-Request-ID"]) == 32
    assert result.headers["X-Request-ID"] in caplog.text
    assert "secret-not-a-number" not in caplog.text and "untrusted-id" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("path,status,trace", [("/existing", 200, "business-trace"), ("/denied", 401, "business-denied-trace")])
async def test_existing_business_trace_and_permission_headers_survive(path, status, trace, caplog):
    caplog.set_level(logging.INFO, logger="qihuo.financial_request")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application()), base_url="http://test") as client:
        result = await client.get(path)
    assert result.status_code == status
    assert result.headers["X-Request-ID"] == trace
    assert trace in caplog.text
    if status == 401:
        assert result.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.asyncio
async def test_valid_body_keeps_success_data_and_unique_request_ids():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application()), base_url="http://test") as client:
        results = [await client.post("/validate", json={"quantity": 2}) for _ in range(2)]
    assert all(result.status_code == 200 and result.json() == {"quantity": 2} for result in results)
    assert all(len(result.headers["X-Request-ID"]) == 32 for result in results)
    assert results[0].headers["X-Request-ID"] != results[1].headers["X-Request-ID"]
