import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("b29_guard_base", ROOT / "scripts/check-stock-b28-preservation.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
guard = module.guard
guard.ALLOWED = frozenset({
    "frontend/src/stores/market.ts", "frontend/src/stores/notification-preferences.ts",
    "frontend/src/components/notifications/big-order-toast.tsx",
    "frontend/src/components/notifications/task-alert-toast.tsx",
    "frontend/src/stores/notification-preferences.test.mjs",
    "frontend/src/lib/futures-alert-policy.ts", "frontend/src/stores/futures-alert-isolation.test.mjs",
})

if __name__ == "__main__":
    raise SystemExit(guard.main())
