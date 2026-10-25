import hashlib
import json
from datetime import date, datetime, timezone

import httpx
import pytest

from app.services.stock_eod_sync import EodSourceError, TeajoinEodSource
from tests.test_stock_eod import catalog, row


def envelope(fields, items):
    return {"code": 0, "data": {"fields": fields.split(","), "items": items, "has_more": False}}


def install_source(monkeypatch, calendar=None):
    responses = {
        "trade_cal": calendar if calendar is not None else envelope("exchange,cal_date,is_open", [["SSE", "20261016", 1]]),
        "stock_basic": envelope("ts_code,symbol,name,industry,exchange,list_date,list_status", [["600001.SH", "600001", "测试股", "金融", "SSE", "20000101", "L"]]),
        "daily": envelope(",".join(row()), [list(row().values())]),
    }
    calls, payloads = [], {}

    async def handler(request):
        name = request.url.path.rsplit("/", 1)[-1]
        calls.append(name)
        body = responses[name]
        response = httpx.Response(200, content=body if isinstance(body, bytes) else json.dumps(body).encode())
        payloads[name] = response.content
        return response

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    return calls, payloads, responses


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"code": False}, {"code": 0.0}, {"code": "0"}])
async def test_provider_success_code_is_strict_integer_not_truthy_equivalent(monkeypatch, change):
    body = {**envelope("exchange,cal_date,is_open", [["SSE", "20261018", 0]]), **change}
    calls, _, _ = install_source(monkeypatch, body)
    with pytest.raises(EodSourceError):
        await TeajoinEodSource("qa-not-a-key").fetch(date(2026, 10, 18))
    assert calls == ["trade_cal"]


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    b'{"code":0,"code":0,"data":{"fields":["exchange","cal_date","is_open"],"items":[["SSE","20261018",0]],"has_more":false}}',
    b'{"code":0,"ignored":NaN,"data":{"fields":["exchange","cal_date","is_open"],"items":[["SSE","20261018",0]],"has_more":false}}',
    envelope("cal_date,is_open", [["20261018", 0]]),
    envelope("exchange,cal_date,is_open", [["CFFEX", "20261018", 0]]),
    envelope("exchange,cal_date,is_open", [["SSE", "20261018", False]]),
    envelope("exchange,cal_date,is_open", [["SSE", "20261018", 0.0]]),
    envelope("exchange,cal_date,is_open", [["SSE", "20261018", 0], ["SSE", "20261019", 1]]),
])
async def test_ambiguous_json_calendar_identity_and_types_never_become_closed_market(monkeypatch, body):
    calls, _, _ = install_source(monkeypatch, body)
    with pytest.raises(EodSourceError) as error:
        await TeajoinEodSource("qa-not-a-key").fetch(date(2026, 10, 18))
    assert calls == ["trade_cal"] and "qa-not-a-key" not in str(error.value)


@pytest.mark.asyncio
async def test_live_source_preserves_raw_response_digests_and_actual_receipt_times(monkeypatch):
    from app.services.stock_eod import normalize_eod

    calls, payloads, _ = install_source(monkeypatch)
    result = await TeajoinEodSource("qa-not-a-key").fetch(date(2026, 10, 16))
    assert calls == ["trade_cal", "stock_basic", "daily"]
    evidence = result.source_evidence
    assert evidence["version"] == "teajoin-eod-response-v1" and evidence["mode"] == "live_http"
    assert len(evidence["responses"]) == 3
    for response in evidence["responses"]:
        assert response["payload_sha256"] == hashlib.sha256(payloads[response["api_name"]]).hexdigest()
        assert response["byte_count"] == len(payloads[response["api_name"]])
        assert datetime.fromisoformat(response["received_at"]).tzinfo is not None
    snapshot = normalize_eod(result.rows, result.catalog, date(2026, 10, 16), acquired_at=datetime.now(timezone.utc), has_more=result.has_more, source_evidence=evidence)
    assert snapshot["source_evidence"] == evidence
    assert "qa-not-a-key" not in json.dumps(snapshot)


@pytest.mark.asyncio
async def test_cached_daily_still_checks_calendar_and_does_not_claim_live_daily_download(monkeypatch):
    calls, _, _ = install_source(monkeypatch)
    cached = envelope(",".join(row()), [list(row().values())])["data"]
    result = await TeajoinEodSource("qa-not-a-key").fetch(date(2026, 10, 16), cached)
    assert calls == ["trade_cal", "stock_basic"]
    assert result.source_evidence["mode"] == "cached_daily"
    assert [response["api_name"] for response in result.source_evidence["responses"]] == calls
    assert len(result.source_evidence["canonical_cached_data_sha256"]) == 64


@pytest.mark.asyncio
async def test_cached_daily_cannot_skip_closed_calendar_or_download_catalog(monkeypatch):
    calls, _, _ = install_source(monkeypatch, envelope("exchange,cal_date,is_open", [["SSE", "20261018", 0]]))
    result = await TeajoinEodSource("qa-not-a-key").fetch(date(2026, 10, 18), {"has_more": False})
    assert result is None and calls == ["trade_cal"]


@pytest.mark.asyncio
async def test_sample_verification_is_one_request_and_three_identifiers_maximum(monkeypatch):
    calls, _, _ = install_source(monkeypatch)
    source = TeajoinEodSource("qa-not-a-key")
    result = await source.sample_daily(["600001.SH"], date(2026, 10, 16))
    assert calls == ["daily"] and result.rows[0]["close"] == 11
    for identifiers in ([], ["600001.SH"] * 2, ["600001.SH", "600002.SH", "600003.SH", "600004.SH"], ["XAUUSD.FXCM"]):
        with pytest.raises(ValueError):
            await source.sample_daily(identifiers, date(2026, 10, 16))
    assert calls == ["daily"]


@pytest.mark.asyncio
async def test_run_eod_persists_evidence_atomically_and_reads_it_with_old_snapshots(monkeypatch):
    from app.services.stock_eod import latest_eod, market_from_eod
    from app.services.stock_eod_sync import run_eod
    from tests.test_stock_eod_integration import database

    install_source(monkeypatch)
    source = TeajoinEodSource("qa-not-a-key")
    async with database() as factory:
        await run_eod(factory, source, date(2026, 10, 16))
        async with factory() as session:
            stored = await latest_eod(session, date(2026, 10, 18))
            assert stored.snapshot_json["source_evidence"]["mode"] == "live_http"
            assert market_from_eod(stored.snapshot_json, catalog())["coverage"]["quoted"] == 1
