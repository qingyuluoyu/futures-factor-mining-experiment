import importlib.util
from pathlib import Path


specification = importlib.util.spec_from_file_location("preservation_guard", Path(__file__).resolve().parents[2] / "scripts/check-futures-preservation.py")
guard = importlib.util.module_from_spec(specification)
specification.loader.exec_module(guard)


def test_protection_covers_futures_shared_code_and_unexpected_additions(tmp_path):
    for name in ("backend/app/services/paper/orders.py", "frontend/src/stores/auth.ts", "frontend/src/components/market/stock-screener-workspace.tsx"):
        file = tmp_path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("baseline", encoding="utf-8")
    before = guard.inventory(tmp_path)
    (tmp_path / "backend/app/services/paper/orders.py").write_text("changed", encoding="utf-8")
    (tmp_path / "frontend/src/stores/auth.ts").unlink()
    (tmp_path / "frontend/src/stores/unexpected.ts").write_text("new", encoding="utf-8")
    delta = guard.differences(before, guard.inventory(tmp_path))
    assert delta == {"changed": ["backend/app/services/paper/orders.py"], "removed": ["frontend/src/stores/auth.ts"], "added": ["frontend/src/stores/unexpected.ts"]}
    assert "frontend/src/components/market/stock-screener-workspace.tsx" not in before


def test_shared_styles_dependency_and_deployment_configuration_are_protected(tmp_path):
    names = ["frontend/src/app/globals.css", "backend/pyproject.toml", "backend/uv.lock", "frontend/tsconfig.json", "frontend/postcss.config.mjs", "docker-compose.yml", "backend/Dockerfile", "frontend/Dockerfile"]
    for name in names:
        file = tmp_path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("baseline", encoding="utf-8")
    before = guard.inventory(tmp_path)
    assert set(names).issubset(before)
    for name in names:
        (tmp_path / name).write_text("changed", encoding="utf-8")
    assert guard.differences(before, guard.inventory(tmp_path))["changed"] == sorted(names)
