import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("stock_b21_base_guard", ROOT / "scripts/check-stock-b20-preservation.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
guard = module.guard
guard.ALLOWED = frozenset({
    "backend/app/api/stock_research_jobs.py", "backend/app/api/stock_research_inputs.py",
    "backend/app/schemas/stock_research_inputs.py", "backend/app/services/stock_research_inputs.py",
    "frontend/src/components/factor-research/factor-research-workspace.tsx",
    "frontend/src/components/factor-research/stock-research-input-check.tsx",
    "frontend/src/lib/stock-research-inputs-api.ts",
    "frontend/src/components/factor-research/stock-research-input-check.test.mjs",
    "frontend/src/lib/stock-research-inputs-api.test.mjs",
})


if __name__ == "__main__":
    raise SystemExit(guard.main())
