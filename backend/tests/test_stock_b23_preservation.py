import importlib.util
from pathlib import Path


def test_b23_freezes_futures_shared_and_replay_matching_and_financial_history(tmp_path):
    spec = importlib.util.spec_from_file_location("b23guard", Path(__file__).resolve().parents[2] / "scripts/check-stock-b23-preservation.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    protected = [".env", ".env.runtime", "backend/app/services/stock_replay.py", "backend/app/services/stock_replay_engine.py",
        "backend/app/schemas/stock_replay.py", "backend/app/models/stock_replay.py", "frontend/src/lib/stock-replay-api.ts",
        "backend/app/services/stock_fundamentals.py", "backend/app/api/stock_market.py", "frontend/src/components/market/stock-quote-detail.tsx",
        "backend/app/services/stock_factors/engine.py", "backend/app/services/paper/orders.py", "frontend/src/lib/api.ts", "frontend/src/stores/auth.ts"]
    for name in [*protected, *module.guard.ALLOWED]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("before", encoding="utf-8")
    before = module.guard.inventory(tmp_path)
    assert set(before) == set(protected)
    for name in protected:
        (tmp_path / name).write_text("after", encoding="utf-8")
    assert module.guard.differences(before, module.guard.inventory(tmp_path))["changed"] == sorted(protected)
