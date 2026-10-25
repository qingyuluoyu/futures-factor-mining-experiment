import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("b32_guard_base", ROOT / "scripts/check-stock-b31-preservation.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
guard = module.guard
guard.ALLOWED = frozenset({
    "backend/app/api/stock_archive_catalog.py", "backend/app/services/stock_raw_panel.py",
    "backend/app/schemas/stock_archive_observations.py", "backend/tests/test_stock_archive_observations.py",
    "frontend/src/components/market/stock-archive-catalog.tsx", "frontend/src/components/market/stock-archive-catalog.test.mjs",
    "frontend/src/components/market/stock-archive-observation.tsx", "frontend/src/components/market/stock-archive-observation.test.mjs",
    "frontend/src/lib/stock-archive-observation-api.ts", "frontend/src/lib/stock-archive-observation-api.test.mjs",
    "scripts/verify-stock-archive-observations-local.py",
})

if __name__ == "__main__":
    raise SystemExit(guard.main())
