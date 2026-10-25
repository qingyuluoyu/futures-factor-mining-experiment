import importlib.util
from pathlib import Path


def test_b16_protects_previous_stock_backend_as_well_as_futures_and_shared_files(tmp_path):
    specification = importlib.util.spec_from_file_location("stock_b16_guard", Path(__file__).resolve().parents[2] / "scripts/check-stock-b16-preservation.py")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    protected = ["backend/app/api/stock_market.py", "backend/app/services/paper/orders.py", "frontend/src/stores/auth.ts", "frontend/src/lib/stock-screen-api.ts", "frontend/src/app/globals.css", "backend/uv.lock"]
    for name in [*protected, "frontend/src/lib/stock-data-availability-api.ts"]:
        file = tmp_path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("before", encoding="utf-8")
    before = module.guard.inventory(tmp_path)
    assert set(before) == set(protected)
    for name in protected:
        (tmp_path / name).write_text("after", encoding="utf-8")
    assert module.guard.differences(before, module.guard.inventory(tmp_path))["changed"] == sorted(protected)


def test_b16_protects_production_migrations_operators_and_futures_bridge(tmp_path):
    specification = importlib.util.spec_from_file_location("stock_b16_deployment_guard", Path(__file__).resolve().parents[2] / "scripts/check-stock-b16-preservation.py")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    names = ["backend/migrations/20261020_009_gp_jobs.sql", "backend/migrations/20261018_002_stock_research_jobs.sql", "backend/scripts/local_service.py", "scripts/ctp-md-bridge.sh", "backend/ctp-bridge.Dockerfile"]
    for name in names:
        file = tmp_path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("before", encoding="utf-8")
    before = module.guard.inventory(tmp_path)
    assert set(names).issubset(before)
    for name in names:
        (tmp_path / name).write_text("after", encoding="utf-8")
    assert module.guard.differences(before, module.guard.inventory(tmp_path))["changed"] == sorted(names)
