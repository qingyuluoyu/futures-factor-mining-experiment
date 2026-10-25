import importlib.util
from pathlib import Path


def test_b17_scope_keeps_futures_shared_migrations_and_old_stock_logic_frozen(tmp_path):
    specification = importlib.util.spec_from_file_location("stock_b17_guard", Path(__file__).resolve().parents[2] / "scripts/check-stock-b17-preservation.py")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    names = ["backend/app/services/stock_eod.py", "backend/app/services/paper/orders.py", "backend/migrations/20261020_009_gp_jobs.sql", "backend/scripts/local_service.py", "scripts/ctp-md-bridge.sh", "backend/ctp-bridge.Dockerfile", "frontend/src/stores/auth.ts", "frontend/src/lib/stock-data-availability-api.ts"]
    for name in [*names, "backend/app/api/stock_market.py"]:
        file = tmp_path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("before", encoding="utf-8")
    before = module.guard.inventory(tmp_path)
    assert set(before) == set(names)
    for name in names:
        (tmp_path / name).write_text("after", encoding="utf-8")
    assert module.guard.differences(before, module.guard.inventory(tmp_path))["changed"] == sorted(names)
