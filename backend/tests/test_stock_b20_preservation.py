import importlib.util
from pathlib import Path


def test_b20_preserves_futures_auth_shared_market_switch_archive_and_config(tmp_path):
    spec = importlib.util.spec_from_file_location("stock_b20_guard", Path(__file__).resolve().parents[2] / "scripts/check-stock-b20-preservation.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    protected = [".env", ".env.runtime", "frontend/src/lib/stock-api.ts", "frontend/src/stores/auth.ts",
        "backend/app/services/paper/account_svc.py", "backend/app/services/ai/gateway.py", "backend/app/services/stock_raw_panel.py",
        "backend/app/services/stock_daily_archive.py", "scripts/export-stock-raw-panel.py", "backend/scripts/stock_eod_operator.py"]
    for name in [*protected, *module.guard.ALLOWED]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("before", encoding="utf-8")
    before = module.guard.inventory(tmp_path)
    assert set(before) == set(protected)
    for name in protected:
        (tmp_path / name).write_text("after", encoding="utf-8")
    assert module.guard.differences(before, module.guard.inventory(tmp_path))["changed"] == sorted(protected)
