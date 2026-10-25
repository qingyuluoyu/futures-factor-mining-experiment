import importlib.util
from pathlib import Path


def test_b18_operator_only_allowlist_keeps_existing_workers_and_futures_frozen(tmp_path):
    specification = importlib.util.spec_from_file_location("stock_b18_guard", Path(__file__).resolve().parents[2] / "scripts/check-stock-b18-preservation.py")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    protected = ["backend/app/services/stock_eod_sync.py", "backend/app/api/stock_market.py", "backend/app/services/paper/orders.py", "backend/scripts/local_service.py", "scripts/ctp-md-bridge.sh", "backend/migrations/20261020_009_gp_jobs.sql", "frontend/src/stores/auth.ts"]
    for name in [*protected, "backend/scripts/stock_eod_operator.py", "scripts/sync-stock-eod-once.py"]:
        file = tmp_path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("before", encoding="utf-8")
    before = module.guard.inventory(tmp_path)
    assert set(before) == set(protected)
    for name in protected:
        (tmp_path / name).write_text("after", encoding="utf-8")
    assert module.guard.differences(before, module.guard.inventory(tmp_path))["changed"] == sorted(protected)


def test_b18_runtime_configuration_is_hash_only_protected_and_deletion_detected(tmp_path):
    specification = importlib.util.spec_from_file_location("stock_b18_config_guard", Path(__file__).resolve().parents[2] / "scripts/check-stock-b18-preservation.py")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    for name in (".env", ".env.runtime"):
        (tmp_path / name).write_text("synthetic-fixture-config", encoding="utf-8")
    before = module.guard.inventory(tmp_path)
    assert set(before) == {".env", ".env.runtime"}
    assert all(len(value) == 64 and "synthetic" not in value for value in before.values())
    (tmp_path / ".env").write_text("modified-fixture-config", encoding="utf-8")
    (tmp_path / ".env.runtime").unlink()
    assert module.guard.differences(before, module.guard.inventory(tmp_path)) == {"changed": [".env"], "removed": [".env.runtime"], "added": []}
