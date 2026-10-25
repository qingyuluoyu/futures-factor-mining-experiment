import asyncio
import inspect
from datetime import date

import httpx
import pytest

from app.services.stock_eod_sync import EodSourceError, TeajoinEodSource


def data(fields, items, has_more=False):
    return {"code": 0, "data": {"fields": fields.split(","), "items": items, "has_more": has_more}}


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [(402, "eod_source_payment_required"), (403, "eod_source_unauthorized"), (429, "eod_source_rate_limited"), (503, "eod_source_unavailable")])
async def test_source_failures_have_actionable_redacted_codes_and_no_retry(monkeypatch, status, code):
    calls = []
    async def handler(request):
        calls.append(request.url.path)
        return httpx.Response(status, text="synthetic-sensitive-provider-message")
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    with pytest.raises(EodSourceError) as caught:
        await TeajoinEodSource("synthetic-not-a-key").fetch(date(2026, 10, 19))
    assert caught.value.code == code
    assert len(calls) == 1
    assert "synthetic-sensitive" not in str(caught.value)


@pytest.mark.asyncio
async def test_eod_source_has_native_cancellation_and_never_calls_next_paid_endpoint(monkeypatch):
    assert inspect.iscoroutinefunction(TeajoinEodSource.fetch)
    requests = []
    started = asyncio.Event()
    async def handler(request):
        requests.append(request.url.path)
        started.set()
        await asyncio.sleep(60)
        return httpx.Response(200, json=data("exchange,cal_date,is_open", [["SSE", "20261016", 1]]))
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    task = asyncio.create_task(TeajoinEodSource("test-not-a-key").fetch(date(2026, 10, 16)))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert requests == ["/debug/api/trade_cal"]


@pytest.mark.asyncio
async def test_eod_closed_calendar_stops_after_one_call(monkeypatch):
    requests = []
    async def handler(request):
        requests.append(request.url.path)
        return httpx.Response(200, json=data("exchange,cal_date,is_open", [["SSE", "20261018", 0]]))
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    assert await TeajoinEodSource("test-not-a-key").fetch(date(2026, 10, 18)) is None
    assert requests == ["/debug/api/trade_cal"]


@pytest.mark.asyncio
async def test_eod_rejects_incomplete_calendar_and_does_not_retry_or_leak_source_errors(monkeypatch):
    requests = []
    async def handler(request):
        requests.append(request.url.path)
        return httpx.Response(200, json={"code": 402, "msg": "private-test-token-payload"})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    with pytest.raises(EodSourceError) as error:
        await TeajoinEodSource("test-not-a-key").fetch(date(2026, 10, 18))
    assert len(requests) == 1 and "private-test-token" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [data("cal_date,cal_date,is_open", [["20261018", "20261018", 0]]), {"oversized": "x" * (8 * 1024 * 1024)}])
async def test_eod_source_rejects_duplicate_fields_and_bounded_stream(monkeypatch, payload):
    requests = []
    async def handler(request):
        requests.append(request.url.path)
        return httpx.Response(200, json=payload)
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    with pytest.raises(EodSourceError):
        await TeajoinEodSource("test-not-a-key").fetch(date(2026, 10, 18))
    assert len(requests) == 1
