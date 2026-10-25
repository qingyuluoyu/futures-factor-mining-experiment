import importlib.util
import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
specification = importlib.util.spec_from_file_location("stock_b18_base_guard", ROOT / "scripts/check-stock-b16-preservation.py")
module = importlib.util.module_from_spec(specification)
specification.loader.exec_module(module)
guard = module.guard
guard.ALLOWED = frozenset({"backend/scripts/stock_eod_operator.py", "scripts/sync-stock-eod-once.py"})
base_inventory = guard.inventory


def inventory(root: Path) -> dict[str, str]:
    current = {name: digest for name, digest in base_inventory(root).items() if name not in guard.ALLOWED}
    for name in (".env", ".env.runtime"):
        path = root / name
        if path.is_file():
            current[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(sorted(current.items()))


guard.inventory = inventory


if __name__ == "__main__":
    raise SystemExit(guard.main())
