import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALLOWED = frozenset({
    "backend/app/api/stock_market.py", "backend/app/schemas/stock_screen.py", "backend/app/services/stock_screen.py",
    "frontend/src/lib/stock-market-api.ts", "frontend/src/lib/stock-screen-api.ts",
    "frontend/src/components/market/stock-screener-workspace.tsx", "frontend/src/components/market/stock-screen-results.tsx",
    "frontend/src/components/market/stock-screener-workspace.test.mjs", "frontend/src/components/market/stock-screen-results.test.mjs",
    "frontend/src/lib/stock-screen-api.test.mjs",
})
EXTRA = ("frontend/package.json", "frontend/pnpm-lock.yaml", "frontend/next.config.ts", "backend/requirements.txt", "scripts/check-futures-preservation.py", "backend/pyproject.toml", "backend/uv.lock", "frontend/tsconfig.json", "frontend/postcss.config.mjs", "docker-compose.yml", "backend/Dockerfile", "frontend/Dockerfile")


def inventory(root: Path) -> dict[str, str]:
    paths = [path for folder in ("backend/app", "frontend/src") for path in (root / folder).rglob("*") if path.is_file() and path.suffix in {".py", ".ts", ".tsx", ".mjs", ".json", ".sql", ".css", ".scss", ".js", ".jsx", ".cjs", ".mts", ".cts", ".html", ".svg"}]
    paths.extend(root / name for name in EXTRA if (root / name).is_file())
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(paths) if path.relative_to(root).as_posix() not in ALLOWED}


def differences(before: dict[str, str], after: dict[str, str]) -> dict[str, list[str]]:
    return {"removed": sorted(before.keys() - after.keys()), "added": sorted(after.keys() - before.keys()), "changed": sorted(name for name in before.keys() & after.keys() if before[name] != after[name])}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("snapshot", "verify"))
    parser.add_argument("manifest", type=Path)
    arguments = parser.parse_args()
    path = arguments.manifest.resolve()
    if path.parent != ROOT / "logs":
        raise ValueError("Manifest must be directly inside workspace logs")
    current = inventory(ROOT)
    if arguments.mode == "snapshot":
        with path.open("x", encoding="utf-8") as destination:
            json.dump({"version": "futures-preservation-b15-v2", "allowed": sorted(ALLOWED), "files": current}, destination, ensure_ascii=False, indent=2)
        print(json.dumps({"status": "snapshotted", "protected_files": len(current)}, ensure_ascii=True))
        return 0
    baseline = json.loads(path.read_text(encoding="utf-8"))
    if baseline.get("version") not in {"futures-preservation-b15-v1", "futures-preservation-b15-v2"} or baseline.get("allowed") != sorted(ALLOWED) or not isinstance(baseline.get("files"), dict):
        raise ValueError("Invalid preservation manifest")
    delta = differences(baseline["files"], current)
    passed = not any(delta.values())
    print(json.dumps({"status": "passed" if passed else "failed", "protected_files": len(current), **delta}, ensure_ascii=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
