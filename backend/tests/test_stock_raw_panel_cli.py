from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def module():
    path = Path(__file__).resolve().parents[2] / "scripts/export-stock-raw-panel.py"
    assert path.is_file(), "Missing bounded read-only raw panel export"
    spec = spec_from_file_location("stock_raw_panel_cli", path)
    result = module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_raw_export_parser_requires_explicit_versions_instruments_and_output():
    parser = module().parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])
    args = parser.parse_args(["--version", "a" * 64, "--instrument", "600001.SH", "--output", "logs/new.json"])
    assert args.version == ["a" * 64] and args.instrument == ["600001.SH"]


def test_raw_export_publishes_complete_json_once_and_never_overwrites(tmp_path):
    target = tmp_path / "panel.json"
    api = module()
    result = {"version": "test", "volume": "0.01"}
    api.publish_manifest(target, result)
    assert json.loads(target.read_text()) == result
    with pytest.raises(FileExistsError):
        api.publish_manifest(target, {"changed": True})
    assert json.loads(target.read_text()) == result
    assert list(tmp_path.iterdir()) == [target]


def test_raw_export_publish_failure_and_bad_json_leave_no_partial_target(monkeypatch, tmp_path):
    api = module()
    def fail(*args):
        raise OSError("controlled publish failure")
    monkeypatch.setattr(api.os, "link", fail)
    target = tmp_path / "new.json"
    with pytest.raises(OSError):
        api.publish_manifest(target, {"value": 1})
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(ValueError):
        api.publish_manifest(target, {"value": float("nan")})
    assert list(tmp_path.iterdir()) == []


def test_raw_export_output_scope_rejects_existing_and_outside(tmp_path):
    api = module()
    root = tmp_path / "logs"
    root.mkdir()
    assert api.output_path(root / "new.json", logs_root=root) == root / "new.json"
    for target in (tmp_path / "else.json", root / "nested" / "new.json", root / "file.txt"):
        with pytest.raises(ValueError):
            api.output_path(target, logs_root=root)
    (root / "new.json").write_text("old")
    with pytest.raises(FileExistsError):
        api.output_path(root / "new.json", logs_root=root)


@pytest.mark.parametrize("host,port", [("remote", 5433), ("localhost", 5432)])
@pytest.mark.asyncio
async def test_raw_export_refuses_other_database_before_connect(monkeypatch, tmp_path, host, port):
    api = module()
    monkeypatch.setattr(api, "load_runtime_settings", lambda: SimpleNamespace(postgres_host=host, postgres_port=port))
    monkeypatch.setattr(api, "create_replay_engine", lambda *_: pytest.fail("No other database access"))
    monkeypatch.setattr(api, "output_path", lambda *_: tmp_path / "new.json")
    with pytest.raises(ValueError, match="5433"):
        await api.run(SimpleNamespace(version=["a" * 64], instrument=["600001.SH"], output="logs/new.json"))
