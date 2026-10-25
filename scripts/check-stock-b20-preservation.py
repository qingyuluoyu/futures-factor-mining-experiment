import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
specification = importlib.util.spec_from_file_location("stock_b20_base_guard", ROOT / "scripts/check-stock-b19-preservation.py")
module = importlib.util.module_from_spec(specification)
specification.loader.exec_module(module)
guard = module.guard
guard.ALLOWED = frozenset({
    "backend/app/api/stock.py", "backend/app/services/stock_eod.py",
    "frontend/src/components/market/stock-quote-detail.tsx", "frontend/src/components/market/stock-kline-chart.tsx",
    "frontend/src/components/trading/stock-replay-workspace.tsx",
    "frontend/src/lib/stock-kline-contract.ts", "frontend/src/lib/stock-price-change.ts",
})


if __name__ == "__main__":
    raise SystemExit(guard.main())
