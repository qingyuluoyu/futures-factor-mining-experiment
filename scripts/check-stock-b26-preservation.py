import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("stock_b26_existing_guard", ROOT / "scripts/check-stock-b23-preservation.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
guard = module.guard
guard.ALLOWED = frozenset({
    "frontend/src/components/market/stock-quote-detail.tsx", "frontend/src/components/market/stock-quote-detail.test.mjs",
    "frontend/src/components/market/stock-board-order.ts", "frontend/src/components/market/stock-board-order.test.mjs",
    "frontend/src/components/market/stock-board-browser.tsx", "frontend/src/components/market/stock-board-browser.test.mjs",
    "frontend/src/components/dashboard/stock-dashboard.tsx", "frontend/src/components/dashboard/stock-dashboard.test.mjs",
})


if __name__ == "__main__":
    raise SystemExit(guard.main())
