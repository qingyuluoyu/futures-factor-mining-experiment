import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("b28_guard_base", ROOT / "scripts/check-stock-b27-preservation.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
guard = module.guard
guard.ALLOWED = frozenset({
    "frontend/src/lib/stock-research-api.ts", "frontend/src/lib/stock-research-api.test.mjs",
    "frontend/src/components/factor-research/stock-research-history.tsx",
    "frontend/src/components/factor-research/stock-research-history.test.mjs",
    "scripts/verify-stock-research-history-local.py",
})

if __name__ == "__main__":
    raise SystemExit(guard.main())
