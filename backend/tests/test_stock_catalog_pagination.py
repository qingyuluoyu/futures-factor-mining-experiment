import importlib.util
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.data.tushare_client import TushareClient


@pytest.fixture
def catalog_import(monkeypatch):
    path = Path(__file__).resolve().parents[2] / "scripts" / "import-stock-catalog.py"
    specification = importlib.util.spec_from_file_location("catalog_import_under_test", path)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    monkeypatch.setattr(module, "load_runtime_settings", lambda: SimpleNamespace(tushare_token="catalog-test-only"))
    requests = []

    def configure(has_more=None):
        data = {
            "fields": ["ts_code", "symbol", "name", "exchange", "industry", "list_date", "list_status"],
            "items": [[f"{index:06d}.SZ", f"{index:06d}", "目录测试", "SZSE", "测试行业", "20000101", "L"] for index in range(1, 1002)],
        }
        if has_more is not None:
            data["has_more"] = has_more

        def respond(request):
            requests.append(request.url.path)
            return httpx.Response(200, json={"code": 0, "data": data})

        def client_factory(token, **options):
            client = TushareClient(token, **options)
            client._client.close()
            client._client = httpx.Client(transport=httpx.MockTransport(respond))
            return client

        monkeypatch.setattr(module, "TushareClient", client_factory)

    def reject_database_access(*arguments, **options):
        raise AssertionError("pagination validation must not open the database")

    monkeypatch.setattr(module, "create_replay_engine", reject_database_access)
    return module, configure, requests


@pytest.mark.parametrize("dry_run", [True, False])
async def test_catalog_rejects_explicit_truncation_before_any_database_access(catalog_import, dry_run):
    module, configure, requests = catalog_import
    configure(True)
    with pytest.raises(ValueError, match="has_more"):
        await module.run(dry_run)
    assert requests == ["/debug/api/stock_basic"]


async def test_catalog_reports_missing_completeness_metadata_without_claiming_full_catalog(catalog_import):
    module, configure, requests = catalog_import
    configure()
    result = await module.run(True)
    assert result["records"] == 1001
    assert result["supplier_has_more"] is None
    assert result["completeness"] == "supplier_not_certified"
    assert result["scope"] == "returned_currently_listed_A_share_records"
    assert "未认证" in result["completeness_note"]
    assert result["history_downloaded"] is False
    assert requests == ["/debug/api/stock_basic"]


async def test_catalog_distinguishes_supplier_reported_completion_from_independent_verification(catalog_import):
    module, configure, _ = catalog_import
    configure(False)
    result = await module.run(True)
    assert result["records"] == 1001
    assert result["supplier_has_more"] is False
    assert result["completeness"] == "supplier_reported_complete"
    assert result["scope"] == "returned_currently_listed_A_share_records"
    assert "未做独立" in result["completeness_note"]
