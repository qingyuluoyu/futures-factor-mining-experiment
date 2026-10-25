import asyncio
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.stock_eod_sync import EodSourceError
from tests.test_stock_eod import row
from tests.test_stock_eod_audit import snapshot


def load_cli(monkeypatch, *, fail=False):
    script = Path(__file__).resolve().parents[2] / "scripts/audit-stock-eod.py"
    spec = importlib.util.spec_from_file_location("stock_audit_claim_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    stored = snapshot()

    class Connection:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        def begin(self):
            return self
        async def execute(self, *args):
            pass
        async def scalar(self, *args):
            return stored

    class Engine:
        sync_engine = SimpleNamespace()
        def connect(self):
            return Connection()
        async def dispose(self):
            pass

    entered, release, calls = asyncio.Event(), asyncio.Event(), []

    class Source:
        def __init__(self, token):
            pass
        async def sample_daily(self, identifiers, trading_date):
            calls.append(identifiers)
            entered.set()
            await release.wait()
            if fail:
                raise EodSourceError("controlled paid boundary failure")
            return SimpleNamespace(rows=[row(ts_code=identifier) for identifier in identifiers], evidence={"api_name": "daily", "received_at": datetime.now(timezone.utc).isoformat()})

    monkeypatch.setattr(module, "load_runtime_settings", lambda: SimpleNamespace(postgres_host="127.0.0.1", postgres_port=5433, tushare_token="qa-private-value"))
    monkeypatch.setattr(module, "create_replay_engine", lambda settings: Engine())
    monkeypatch.setattr(module, "TeajoinEodSource", Source)
    return module, calls, entered, release


@pytest.mark.asyncio
async def test_concurrent_same_output_claims_real_file_before_one_paid_request(monkeypatch, tmp_path):
    module, calls, entered, release = load_cli(monkeypatch)
    output = tmp_path / "audit.json"
    args = SimpleNamespace(output=str(output), date=None, verify_source=True)
    tasks = [asyncio.create_task(module.run(args)) for _ in range(2)]
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.sleep(0)
        release.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 2)
    finally:
        release.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert len(calls) == 1
    assert sum(isinstance(result, FileExistsError) for result in results) == 1
    assert json.loads(output.read_text())["status"] == "matched"
    claim = json.loads(output.with_name(output.name + ".claim.json").read_text())
    assert claim["phase"] == "completed" and claim["source_attempts"] == 1
    assert "qa-private-value" not in json.dumps(claim)


@pytest.mark.asyncio
async def test_paid_failure_leaves_durable_claim_and_same_output_never_recharges(monkeypatch, tmp_path):
    module, calls, _, release = load_cli(monkeypatch, fail=True)
    release.set()
    output = tmp_path / "failed.json"
    args = SimpleNamespace(output=str(output), date=None, verify_source=True)
    with pytest.raises(EodSourceError):
        await module.run(args)
    assert not output.exists()
    with pytest.raises(FileExistsError):
        await module.run(args)
    assert len(calls) == 1
    claim = json.loads(output.with_name(output.name + ".claim.json").read_text())
    assert claim["phase"] == "failed" and claim["source_attempts"] == 1
    assert claim["error_type"] == "EodSourceError"
