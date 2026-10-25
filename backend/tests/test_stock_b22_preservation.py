import importlib.util
from pathlib import Path


def test_b22_only_allows_stock_history_slice_and_freezes_prior_research_and_futures(tmp_path):
    spec = importlib.util.spec_from_file_location("b22guard", Path(__file__).resolve().parents[2] / "scripts/check-stock-b22-preservation.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    protected = [".env", ".env.runtime", "backend/app/services/stock_factors/engine.py", "backend/app/services/stock_eod.py",
        "backend/app/api/stock_research_jobs.py", "backend/app/services/stock_research_inputs.py", "backend/app/api/stock.py",
        "frontend/src/components/factor-research/factor-research-workspace.tsx", "frontend/src/lib/stock-api.ts",
        "frontend/src/lib/stock-market-api.ts", "backend/app/services/paper/orders.py", "backend/app/services/ai/gateway.py", "frontend/src/stores/auth.ts"]
    for name in [*protected, *module.guard.ALLOWED]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("before", encoding="utf-8")
    before = module.guard.inventory(tmp_path)
    assert set(before) == set(protected)
    for name in protected:
        (tmp_path / name).write_text("after", encoding="utf-8")
    assert module.guard.differences(before, module.guard.inventory(tmp_path))["changed"] == sorted(protected)
