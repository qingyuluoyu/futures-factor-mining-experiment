import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
specification = importlib.util.spec_from_file_location("stock_b19_base_guard", ROOT / "scripts/check-stock-b18-preservation.py")
module = importlib.util.module_from_spec(specification)
specification.loader.exec_module(module)
guard = module.guard
guard.ALLOWED = frozenset({"backend/app/services/stock_raw_panel.py", "scripts/export-stock-raw-panel.py"})


if __name__ == "__main__":
    raise SystemExit(guard.main())
