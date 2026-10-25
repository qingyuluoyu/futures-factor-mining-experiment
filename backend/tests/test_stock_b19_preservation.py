import importlib.util
from pathlib import Path


def test_b19_only_allows_new_raw_reader_and_export_and_protects_previous_operations(tmp_path):
    spec = importlib.util.spec_from_file_location("stock_b19_guard", Path(__file__).resolve().parents[2] / "scripts/check-stock-b19-preservation.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    protected = [".env", ".env.runtime", "backend/scripts/stock_eod_operator.py", "scripts/sync-stock-eod-once.py",
        "backend/app/services/stock_daily_archive.py", "backend/app/api/stock_market.py", "backend/app/services/paper/orders.py",
        "scripts/archive-stock-eod.py", "backend/migrations/20261019_004_stock_daily_archive.sql", "frontend/src/stores/auth.ts"]
    for name in [*protected, "backend/app/services/stock_raw_panel.py", "scripts/export-stock-raw-panel.py"]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic-before", encoding="utf-8")
    before = module.guard.inventory(tmp_path)
    assert set(before) == set(protected)
    for name in protected:
        (tmp_path / name).write_text("synthetic-after", encoding="utf-8")
    assert module.guard.differences(before, module.guard.inventory(tmp_path))["changed"] == sorted(protected)
