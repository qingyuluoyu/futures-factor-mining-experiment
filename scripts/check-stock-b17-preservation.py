import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
specification = importlib.util.spec_from_file_location("stock_b17_base_guard", ROOT / "scripts/check-stock-b16-preservation.py")
module = importlib.util.module_from_spec(specification)
specification.loader.exec_module(module)
guard = module.guard
guard.ALLOWED = frozenset({
    "backend/app/api/stock_market.py", "backend/app/schemas/stock_eod_gaps.py", "backend/app/services/stock_eod_gaps.py",
    "frontend/src/lib/stock-eod-gaps-api.ts", "frontend/src/lib/stock-eod-gaps-api.test.mjs",
    "frontend/src/components/market/stock-eod-gap-browser.tsx", "frontend/src/components/market/stock-eod-gap-browser.test.mjs",
    "frontend/src/components/market/stock-data-availability.tsx", "frontend/src/components/market/stock-data-availability.test.mjs",
})


if __name__ == "__main__":
    raise SystemExit(guard.main())
