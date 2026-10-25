from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


def cli_module():
    path = Path(__file__).resolve().parents[2] / "scripts/archive-stock-eod.py"
    assert path.exists(), "Explicit dry-run archive entry point is missing"
    spec = spec_from_file_location("archive_stock_eod_cli", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_archive_cli_requires_source_id_expected_version_and_explicit_apply():
    parser = cli_module().parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])
    with pytest.raises(SystemExit):
        parser.parse_args(["--batch-id", "not-uuid", "--expected-data-version", "a" * 64])
    with pytest.raises(SystemExit):
        parser.parse_args(["--batch-id", "14831b03-e618-4f9d-a89b-f6db88f3c62b", "--expected-data-version", "not-hash"])
    args = parser.parse_args(["--batch-id", "14831b03-e618-4f9d-a89b-f6db88f3c62b", "--expected-data-version", "a" * 64])
    assert args.apply is False and str(args.batch_id) == "14831b03-e618-4f9d-a89b-f6db88f3c62b"
    assert parser.parse_args(["--batch-id", str(args.batch_id), "--expected-data-version", "a" * 64, "--apply"]).apply is True


@pytest.mark.parametrize("host,port", [("remote", 5433), ("127.0.0.1", 5432)])
@pytest.mark.asyncio
async def test_archive_cli_rejects_other_database_before_connecting(monkeypatch, host, port):
    from types import SimpleNamespace
    module = cli_module()
    monkeypatch.setattr(module, "load_runtime_settings", lambda: SimpleNamespace(postgres_host=host, postgres_port=port))
    monkeypatch.setattr(module, "create_replay_engine", lambda *_: pytest.fail("Cannot access a different database"))
    with pytest.raises(ValueError, match="5433"):
        await module.run(SimpleNamespace())
