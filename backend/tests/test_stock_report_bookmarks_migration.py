import importlib.util
from pathlib import Path
import sys

import pytest
from pydantic import ValidationError

from app.models.stock_report_bookmark import BookmarkBase
from app.models.stock_research_job import ResearchJobBase
from app.schemas.stock_report_bookmarks import ReportBookmarkView, SetReportBookmark

ROOT = Path(__file__).resolve().parents[1]


def migration_module():
    spec = importlib.util.spec_from_file_location("bookmark_migration_test", ROOT / "scripts/migrate_stock_report_bookmarks.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bookmark_migration_is_additive_and_metadata_does_not_change_legacy_jobs():
    module = migration_module()
    source = module.SCHEMA_PATH.read_text(encoding="utf-8")
    assert "CREATE TABLE stock_report_bookmarks" in source
    assert "REFERENCES stock_research_jobs(id) ON DELETE RESTRICT" in source
    assert "ALTER TABLE" not in source and "DROP " not in source and "UPDATE " not in source
    assert set(BookmarkBase.metadata.tables) == {"stock_report_bookmarks", "stock_report_bookmark_schema_versions"}
    assert set(ResearchJobBase.metadata.tables) == {"stock_research_jobs", "stock_research_schema_versions"}


@pytest.mark.parametrize("arguments,expected", [([], True), (["--check"], True), (["--apply"], False)])
def test_bookmark_migration_defaults_to_check_not_apply(monkeypatch, arguments, expected):
    module = migration_module()
    calls = []

    async def run(check):
        calls.append(check)

    monkeypatch.setattr(module, "run", run)
    monkeypatch.setattr(sys, "argv", ["migration", *arguments])
    assert module.main() == 0
    assert calls == [expected]


def test_bookmark_input_and_return_contracts_are_coherent():
    assert SetReportBookmark(bookmarked=False).bookmarked is False
    for value in ("false", 0, None):
        with pytest.raises(ValidationError):
            SetReportBookmark(bookmarked=value)
    with pytest.raises(ValidationError):
        ReportBookmarkView(job_id="11111111-1111-4111-8111-111111111111", market="stock", bookmarked=True, created_at=None)
