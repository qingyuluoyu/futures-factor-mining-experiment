import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("b31_guard_base", ROOT / "scripts/check-stock-b30-preservation.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
guard = module.guard
guard.ALLOWED = frozenset({
    "backend/app/api/stock_market.py", "backend/app/api/stock_archive_catalog.py",
    "backend/app/schemas/stock_archive_catalog.py", "backend/app/services/stock_archive_catalog.py",
    "backend/tests/test_stock_archive_catalog.py", "scripts/verify-stock-archive-catalog-local.py",
    "frontend/src/components/market/stock-data-availability.tsx", "frontend/src/components/market/stock-data-availability.test.mjs",
    "frontend/src/components/market/stock-archive-catalog.tsx", "frontend/src/components/market/stock-archive-catalog.test.mjs",
    "frontend/src/lib/stock-archive-catalog-api.ts", "frontend/src/lib/stock-archive-catalog-api.test.mjs",
})

if __name__ == "__main__":
    raise SystemExit(guard.main())
