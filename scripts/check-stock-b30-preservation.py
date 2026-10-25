import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("b30_guard_base", ROOT / "scripts/check-stock-b29-preservation.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
guard = module.guard
guard.ALLOWED = frozenset({
    "backend/app/api/stock_research_reports.py", "backend/app/models/stock_report_bookmark.py",
    "backend/app/schemas/stock_report_bookmarks.py", "backend/app/services/stock_report_bookmarks.py",
    "backend/migrations/20261022_014_stock_report_bookmarks.sql", "backend/scripts/migrate_stock_report_bookmarks.py",
    "backend/tests/test_stock_report_bookmarks.py", "backend/tests/test_stock_report_bookmarks_migration.py",
    "frontend/src/lib/stock-research-api.ts", "frontend/src/lib/stock-research-api.test.mjs",
    "frontend/src/components/factor-research/stock-research-history.tsx",
    "frontend/src/components/factor-research/stock-research-history.test.mjs",
    "frontend/src/components/factor-research/research-report-bookmark.tsx",
    "frontend/src/components/factor-research/research-report-bookmark.test.mjs",
    "scripts/verify-stock-report-bookmarks-local.py",
})

if __name__ == "__main__":
    raise SystemExit(guard.main())
