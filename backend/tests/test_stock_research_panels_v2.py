from uuid import uuid4

import pytest

from app.services.stock_research_jobs import JobError
from tests.test_stock_research_archive_v2 import page, prepare, service
from tests.test_stock_research_job_service import job_database


async def snapshot(archive, *, extra=None):
    from app.schemas.stock_research_v2 import DatasetBuild
    rows = [
        {"instrument_id": "000001.SZ", "effective_date": "2026-01-05", "values": {"close": "12.000000000000000001", "volume": "0.01"}},
        {"instrument_id": "000001.SZ", "effective_date": "2026-01-06", "values": {"close": "13", "volume": None}},
        {"instrument_id": "600000.SH", "effective_date": "2026-01-05", "values": {"close": "8", "volume": "0"}},
    ]
    first = await archive.archive_page(page(collected_at="2026-01-06T08:00:00Z", observations=rows), idempotency_key=uuid4(), request_id="a" * 32)
    sources = [first.source_id]
    if extra is not None:
        sources.append((await archive.archive_page(extra, idempotency_key=uuid4(), request_id="b" * 32)).source_id)
    await archive.session.commit()
    command = DatasetBuild(source_ids=sources, start="2026-01-05", end="2026-01-06", warmup_start="2026-01-05", pool_id="qa-fixed", pool_version="a" * 64, instruments=["600000.SH", "000001.SZ"])
    ref = await archive.build_dataset(command, idempotency_key=uuid4(), request_id="c" * 32)
    await archive.session.commit()
    return ref, first


@pytest.mark.asyncio
async def test_fixed_panel_is_ordered_batched_precise_and_distinguishes_missing_from_real_zero(tmp_path):
    from app.schemas.stock_dataset_inspection import PanelQuery
    from app.services.stock_research_v2.panels import load_panel
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        async with factory() as session:
            archive = service(session, owner, tmp_path / "artifacts")
            ref, source = await snapshot(archive)
            query = PanelQuery(fields=["volume", "close"], batch_rows=2)
            first = await load_panel(archive, ref, query)
            second = await load_panel(archive, ref, query)
            assert list(first.batches()) == list(second.batches())
            batches = list(first.batches())
            assert len(batches) == 2 and all(len(batch) <= 2 for batch in batches)
            rows = [row for batch in batches for row in batch]
            assert [(row["date"], row["instrument_id"]) for row in rows] == [("2026-01-05", "000001.SZ"), ("2026-01-05", "600000.SH"), ("2026-01-06", "000001.SZ"), ("2026-01-06", "600000.SH")]
            assert rows[0]["values"]["close"] == "12.000000000000000001"
            assert rows[0]["values"]["volume"] == "0.01"
            assert rows[1]["values"]["volume"] == "0"
            assert rows[2]["values"]["volume"] is None and rows[3]["values"]["close"] is None
            assert first.summary["date_basis"] == "observed_not_verified_calendar"
            assert first.summary["fields"]["volume"] == {"observed": 3, "valid": 2, "source_null": 1, "absent": 1, "excluded_domain": 0}


@pytest.mark.asyncio
async def test_field_projection_domain_rules_limits_and_scope_never_silently_change_query(tmp_path):
    from app.schemas.stock_dataset_inspection import PanelQuery
    from app.services.stock_research_v2.panels import load_panel
    valuation = page(kind="valuation", price_adjustment="not_applicable", units={"pb": "ratio"}, observations=[{"instrument_id": "000001.SZ", "effective_date": "2026-01-05", "values": {"pb": "-1"}}])
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        async with factory() as session:
            archive = service(session, owner, tmp_path / "artifacts")
            ref, source = await snapshot(archive, extra=valuation)
            panel = await load_panel(archive, ref, PanelQuery(fields=["pb"], instruments=["000001.SZ"]))
            assert panel.summary["fields"]["pb"]["excluded_domain"] == 1
            assert list(panel.batches())[0][0]["values"] == {"pb": None}
            for query in (PanelQuery(fields=["close"], instruments=["000002.SZ"]), PanelQuery(fields=["close"], start="2025-12-31"), PanelQuery(fields=["roe"])):
                with pytest.raises(JobError):
                    await load_panel(archive, ref, query)
            with pytest.raises(JobError) as budget:
                await load_panel(archive, ref, PanelQuery(fields=["close", "volume"]), max_cells=2)
            assert budget.value.code == "panel_budget_exceeded"
            with pytest.raises(JobError) as denied:
                await load_panel(service(session, other, tmp_path / "artifacts"), ref, PanelQuery(fields=["close"]))
            assert denied.value.status == 404


@pytest.mark.asyncio
async def test_conflicting_sources_or_changed_old_files_do_not_become_latest_panel(tmp_path):
    from app.schemas.stock_dataset_inspection import PanelQuery
    from app.services.stock_research_v2.panels import load_panel
    conflicting = page(collected_at="2026-01-06T08:00:00Z", observations=[{"instrument_id": "000001.SZ", "effective_date": "2026-01-05", "values": {"close": "99", "volume": "0.01"}}])
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        async with factory() as session:
            archive = service(session, owner, tmp_path / "artifacts")
            ref, source = await snapshot(archive, extra=conflicting)
            with pytest.raises(JobError) as conflict:
                await load_panel(archive, ref, PanelQuery(fields=["close"]))
            assert conflict.value.code == "conflicting_observations"
            (tmp_path / "artifacts" / source.artifact.relative_path).write_text("{}", encoding="utf8")
            with pytest.raises(JobError) as corrupt:
                await load_panel(archive, ref, PanelQuery(fields=["close"]))
            assert corrupt.value.code == "artifact_integrity_failed"


def test_panel_query_has_explicit_schema_and_cannot_request_future_or_unknown_operations():
    from app.schemas.stock_dataset_inspection import PanelQuery
    for value in ({"fields": ["__class__"]}, {"fields": ["close", "close"]}, {"fields": ["close"], "batch_rows": True}, {"fields": ["close"], "owner_id": str(uuid4())}):
        with pytest.raises(ValueError):
            PanelQuery.model_validate(value)
