import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("stock_b22_base_guard", ROOT / "scripts/check-stock-b21-preservation.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
guard = module.guard
guard.ALLOWED = frozenset({
    "backend/app/api/stock_market.py", "backend/app/api/stock_fundamentals.py",
    "backend/app/schemas/stock_fundamentals.py", "backend/app/services/stock_fundamentals.py",
    "frontend/src/components/market/stock-quote-detail.tsx", "frontend/src/components/market/stock-quote-detail.test.mjs",
    "frontend/src/components/market/stock-fundamentals-history.tsx", "frontend/src/components/market/stock-fundamentals-history.test.mjs",
    "frontend/src/lib/stock-fundamentals-api.ts", "frontend/src/lib/stock-fundamentals-api.test.mjs",
})


if __name__ == "__main__":
    raise SystemExit(guard.main())
