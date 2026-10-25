import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("b27_guard_base", ROOT / "scripts/check-stock-b26-preservation.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
guard = module.guard
guard.ALLOWED = frozenset({"frontend/src/lib/stock-market-api.ts", "frontend/src/lib/stock-market-api.test.mjs",
    "frontend/src/lib/stock-screen-api.ts", "scripts/verify-stock-market-contract-local.py"})

if __name__ == "__main__":
    raise SystemExit(guard.main())
