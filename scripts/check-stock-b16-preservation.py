import importlib.util
import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
specification = importlib.util.spec_from_file_location("stock_b16_base_guard", ROOT / "scripts/check-futures-preservation.py")
guard = importlib.util.module_from_spec(specification)
specification.loader.exec_module(guard)
guard.ALLOWED = frozenset({
    "frontend/src/lib/stock-market-api.ts", "frontend/src/lib/stock-replay-api.ts",
    "frontend/src/lib/stock-data-availability-api.ts", "frontend/src/lib/stock-data-availability-api.test.mjs",
    "frontend/src/components/market/stock-screener-workspace.tsx", "frontend/src/components/market/stock-screener-workspace.test.mjs",
    "frontend/src/components/market/stock-data-availability.tsx", "frontend/src/components/market/stock-data-availability.test.mjs",
})
guard.EXTRA = (*guard.EXTRA, "scripts/check-stock-b16-preservation.py", "backend/ctp-bridge.Dockerfile")
base_inventory = guard.inventory


def inventory(root: Path) -> dict[str, str]:
    current = base_inventory(root)
    paths = [path for folder in ("backend/migrations", "backend/scripts") for path in (root / folder).rglob("*") if path.is_file() and path.suffix in {".py", ".sql", ".sh", ".ps1", ".json", ".yaml", ".yml"}]
    paths.extend(path for path in (root / "scripts").glob("*") if path.is_file() and (path.suffix in {".sh", ".ps1"} or path.suffix == ".py" and not path.name.startswith("test-")))
    current.update({path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})
    return dict(sorted(current.items()))


guard.inventory = inventory


if __name__ == "__main__":
    raise SystemExit(guard.main())
