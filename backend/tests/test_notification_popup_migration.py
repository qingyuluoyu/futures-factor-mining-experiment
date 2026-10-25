from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest


def migration_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "migrate_notification_popup.py"
    specification = spec_from_file_location("notification_popup_migration_tests", path)
    module = module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


@pytest.mark.parametrize("host,port", [("prod.example", 5433), ("localhost", 5432), ("127.0.0.1", 5432)])
def test_cli_rejects_non_local_project_database(host, port):
    with pytest.raises(RuntimeError, match="local PostgreSQL"):
        migration_module().assert_local_settings(SimpleNamespace(postgres_host=host, postgres_port=port))


def test_migration_is_single_additive_statement_and_cli_accepts_local_project_database():
    module = migration_module()
    module.assert_local_settings(SimpleNamespace(postgres_host="127.0.0.1", postgres_port=5433))
    statements = [statement.strip() for statement in module.MIGRATION_PATH.read_text().split(";") if statement.strip()]
    assert len(statements) == 1
    assert statements[0] == "ALTER TABLE user_notify_settings ADD COLUMN IF NOT EXISTS notify_popup_enabled BOOLEAN NOT NULL DEFAULT TRUE"
