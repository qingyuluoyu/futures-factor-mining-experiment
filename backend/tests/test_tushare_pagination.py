import json

import httpx
import pytest

from app.data.tushare_client import TushareClient, TushareError


@pytest.fixture
def client_factory():
    clients = []

    def create(data, observed=None):
        def respond(request):
            if observed is not None:
                observed.append({"path": request.url.path, "body": json.loads(request.content)})
            return httpx.Response(200, json={"code": 0, "data": data})

        client = TushareClient("pagination-test-only", max_retries=1, min_interval=0)
        client._client.close()
        client._client = httpx.Client(transport=httpx.MockTransport(respond))
        clients.append(client)
        return client

    yield create
    for client in clients:
        client.close()


@pytest.mark.parametrize("has_more", [True, False, None])
def test_query_page_preserves_supplier_pagination_metadata(client_factory, has_more):
    data = {"fields": ["ts_code", "name"], "items": [["000001.SZ", "平安银行"]]}
    if has_more is not None:
        data["has_more"] = has_more
    page = client_factory(data).query_page("stock_basic", {"list_status": "L"})
    assert page.records == [{"ts_code": "000001.SZ", "name": "平安银行"}]
    assert page.has_more is has_more


@pytest.mark.parametrize("has_more", ["false", "true", 0, 1, [], {}])
def test_query_page_rejects_malformed_completeness_metadata(client_factory, has_more):
    client = client_factory({"fields": [], "items": [], "has_more": has_more})
    with pytest.raises(TushareError, match="has_more"):
        client.query_page("stock_basic")


def test_legacy_query_stays_a_list_and_keeps_request_contract(client_factory):
    observed = []
    client = client_factory({
        "fields": ["ts_code", "close"], "items": [["000001.SZ", 10.5]], "has_more": True,
    }, observed)
    result = client.query("daily", {"ts_code": "000001.SZ"}, "ts_code,close")
    assert isinstance(result, list)
    assert result == [{"ts_code": "000001.SZ", "close": 10.5}]
    assert observed == [{
        "path": "/debug/api/daily",
        "body": {"api_name": "daily", "token": "pagination-test-only", "params": {"ts_code": "000001.SZ"}, "fields": "ts_code,close"},
    }]


def test_legacy_query_does_not_require_pagination_metadata(client_factory):
    client = client_factory({"fields": ["value"], "items": [[1]], "has_more": "legacy-format"})
    assert client.query("daily") == [{"value": 1}]
