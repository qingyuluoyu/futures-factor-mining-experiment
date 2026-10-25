import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("b33_guard_base", ROOT / "scripts/check-stock-b32-preservation.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
guard = module.guard
guard.ALLOWED = frozenset()

if __name__ == "__main__":
    raise SystemExit(guard.main())
