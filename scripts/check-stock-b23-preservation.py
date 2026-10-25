import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("stock_b23_base_guard", ROOT / "scripts/check-stock-b22-preservation.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
guard = module.guard
guard.ALLOWED = frozenset({
    "backend/app/api/stock_replay.py", "backend/app/api/stock_replay_history.py",
    "backend/app/schemas/stock_replay_history.py", "backend/app/services/stock_replay_history.py",
    "frontend/src/components/trading/stock-replay-workspace.tsx",
    "frontend/src/components/trading/stock-replay-order-history.tsx", "frontend/src/components/trading/stock-replay-order-history.test.mjs",
    "frontend/src/lib/stock-replay-history-api.ts", "frontend/src/lib/stock-replay-history-api.test.mjs",
})


if __name__ == "__main__":
    raise SystemExit(guard.main())
