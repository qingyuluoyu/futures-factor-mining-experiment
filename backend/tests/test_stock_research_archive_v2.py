import hashlib
from uuid import uuid4

import pytest
from sqlalchemy import select, func

from app.services.stock_research_jobs import JobError
from tests.test_stock_research_job_service import job_database


def page(**changes):
    from app.schemas.stock_research_v2 import SourcePage
    value = {"provider": "teajoin", "source_method": "qa_normalized_fixture", "authorization_reference": "qa-only-not-live-authorization", "source_schema_reference": "qa-only-schema", "kind": "daily", "stream_key": "qa-000001-20260105", "collected_at": "2026-01-05T08:00:00Z", "price_adjustment": "raw", "units": {"close": "CNY/share", "volume": "share"},
        "observations": [{"instrument_id": "000001.SZ", "effective_date": "2026-01-05", "values": {"close": "12.000000000000000001", "volume": "0.01"}}], **changes}
    return SourcePage.model_validate(value)


async def prepare(factory):
    from app.models.stock_research_dataset import DatasetBase, DatasetSchemaVersion
    from app.services.stock_research_v2.archive import SCHEMA_PATH, SCHEMA_VERSION
    async with factory() as session:
        DatasetBase.metadata.create_all(session.get_bind())
        session.add(DatasetSchemaVersion(version=SCHEMA_VERSION, checksum=hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()))
        await session.commit()


def service(session, owner, root, mode="virtual"):
    from app.schemas.stock_research_v2 import Scope
    from app.services.stock_research_v2.archive import ArchiveService
    from app.services.stock_research_v2.artifacts import ArtifactStore
    return ArchiveService(session, Scope(owner_id=owner, trading_mode=mode), ArtifactStore(root))


@pytest.mark.asyncio
async def test_unmigrated_registry_makes_no_artifact_and_unregistered_file_is_never_readable(tmp_path):
    async with job_database() as (factory, owner, other), factory() as session:
        archive = service(session, owner, tmp_path / "artifacts")
        with pytest.raises(JobError) as unavailable:
            await archive.archive_page(page(), idempotency_key=uuid4(), request_id="a" * 32)
        assert unavailable.value.code == "dataset_schema_unavailable"
        assert list((tmp_path / "artifacts").iterdir()) == []
        await prepare(factory)
        archive.store.put_json({"qa": "orphan-not-authorized"})
        with pytest.raises(JobError) as absent:
            await archive.read_artifact(uuid4())
        assert absent.value.status == 404


@pytest.mark.asyncio
async def test_source_append_versions_scope_idempotency_and_precise_values(tmp_path):
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        async with factory() as session:
            archive = service(session, owner, tmp_path / "artifacts")
            key = uuid4()
            first = await archive.archive_page(page(), idempotency_key=key, request_id="a" * 32)
            await session.commit()
            assert (await archive.archive_page(page(), idempotency_key=key, request_id="b" * 32)) == first
            await session.commit()
            revised = page(collected_at="2026-01-06T08:00:00Z", revises_id=str(first.source_id), observations=[{"instrument_id": "000001.SZ", "effective_date": "2026-01-05", "values": {"close": "13", "volume": "0.02"}}])
            second = await archive.archive_page(revised, idempotency_key=uuid4(), request_id="c" * 32)
            await session.commit()
            assert second.source_id != first.source_id
            original = await archive.read_source(first.source_id)
            assert original.observations[0].values["close"] == "12.000000000000000001"
            assert original.observations[0].values["volume"] == "0.01"
            assert (await archive.read_source(second.source_id)).revises_id == first.source_id
            with pytest.raises(JobError) as collision:
                await archive.archive_page(revised, idempotency_key=key, request_id="d" * 32)
            assert collision.value.code == "idempotency_conflict"
            await session.rollback()
            for identity, mode in ((other, "virtual"), (owner, "live")):
                scoped = service(session, identity, tmp_path / "artifacts", mode)
                with pytest.raises(JobError) as denied:
                    await scoped.read_artifact(first.artifact.artifact_id)
                assert denied.value.status == 404
                with pytest.raises(JobError):
                    await scoped.archive_page(revised, idempotency_key=uuid4(), request_id="e" * 32)
                await session.rollback()


@pytest.mark.asyncio
async def test_dataset_pins_original_sources_and_cannot_claim_backtest_qualification(tmp_path):
    from app.schemas.stock_research_v2 import DatasetBuild
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        async with factory() as session:
            archive = service(session, owner, tmp_path / "artifacts")
            source = await archive.archive_page(page(), idempotency_key=uuid4(), request_id="a" * 32)
            await session.commit()
            command = DatasetBuild(source_ids=[source.source_id], start="2026-01-05", end="2026-01-05", warmup_start="2026-01-05", pool_id="qa-fixed", pool_version="a" * 64, instruments=["000001.SZ"])
            key = uuid4()
            dataset = await archive.build_dataset(command, idempotency_key=key, request_id="b" * 32)
            await session.commit()
            assert dataset.qualification == "observation_only"
            assert (await archive.build_dataset(command, idempotency_key=key, request_id="c" * 32)) == dataset
            await session.commit()
            manifest = await archive.read_dataset(dataset)
            assert manifest["sources"][0]["sha256"] == source.artifact.sha256
            assert manifest["warmup_start"] == "2026-01-05"
            assert (await archive.list_datasets(limit=20, offset=50))["total"] == 1
            assert (await archive.list_datasets(limit=20, offset=50))["items"] == []
            with pytest.raises(JobError):
                await archive.read_dataset(dataset.model_copy(update={"data_version": "f" * 64}))
            with pytest.raises(JobError):
                await archive.read_dataset(dataset.model_copy(update={"qualification": "backtest_ready"}))
            with pytest.raises(JobError):
                await service(session, other, tmp_path / "artifacts").read_dataset(dataset)


@pytest.mark.asyncio
async def test_db_rollback_leaves_only_unreadable_orphan_then_same_key_recovers(tmp_path):
    from app.models.stock_research_dataset import StockResearchArtifact
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        key = uuid4()
        async with factory() as session:
            archive = service(session, owner, tmp_path / "artifacts")
            lost = await archive.archive_page(page(), idempotency_key=key, request_id="a" * 32)
            await session.rollback()
            assert len(list((tmp_path / "artifacts").glob("*.json"))) == 1
            assert await session.scalar(select(func.count()).select_from(StockResearchArtifact)) == 0
            with pytest.raises(JobError):
                await archive.read_artifact(lost.artifact.artifact_id)
            recovered = await archive.archive_page(page(), idempotency_key=key, request_id="b" * 32)
            await session.commit()
            assert recovered.artifact.sha256 == lost.artifact.sha256
            assert len(list((tmp_path / "artifacts").glob("*.json"))) == 1
            assert (await archive.read_source(recovered.source_id)).provider == "teajoin"


@pytest.mark.asyncio
async def test_corrupt_or_relabelled_registered_artifact_fails_closed(tmp_path):
    from app.models.stock_research_dataset import StockResearchArtifact
    from sqlalchemy.exc import IntegrityError
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        async with factory() as session:
            archive = service(session, owner, tmp_path / "artifacts")
            source = await archive.archive_page(page(), idempotency_key=uuid4(), request_id="a" * 32)
            await session.commit()
            row = await session.scalar(select(StockResearchArtifact).where(StockResearchArtifact.id == source.artifact.artifact_id))
            row.relative_path = "../outside.json"
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()
            row = await session.scalar(select(StockResearchArtifact).where(StockResearchArtifact.id == source.artifact.artifact_id))
            row.sha256, row.relative_path = "f" * 64, "f" * 64 + ".json"
            await session.commit()
            with pytest.raises(JobError) as mismatch:
                await archive.read_source(source.source_id)
            assert mismatch.value.code == "artifact_integrity_failed"
            row.sha256, row.relative_path = source.artifact.sha256, source.artifact.relative_path
            await session.commit()
            (tmp_path / "artifacts" / source.artifact.relative_path).write_text("{}", encoding="utf8")
            with pytest.raises(JobError):
                await archive.read_source(source.source_id)


@pytest.mark.parametrize("changes", [{"units": {"close": "unknown", "volume": "share"}}, {"observations": [{"instrument_id": "000001.SH", "effective_date": "2026-01-05", "values": {"close": "12"}}]}, {"observations": [{"instrument_id": "000001.SZ", "effective_date": "2026-01-05", "values": {"close": 12.0, "volume": "0.01"}}]}, {"price_adjustment": "qfq"}, {"collected_at": "2026-01-05T08:00:00"}])
def test_source_contract_rejects_unknown_units_unmatched_identity_lossy_numbers_and_unknown_anchor(changes):
    with pytest.raises(ValueError):
        page(**changes)


@pytest.mark.parametrize("values", [[], ["close"], {"close": True}, {"volume": "-1"}, {"close": "0"}])
def test_invalid_numeric_shapes_never_escape_as_attribute_errors_or_valid_rows(values):
    with pytest.raises(ValueError):
        page(observations=[{"instrument_id": "000001.SZ", "effective_date": "2026-01-05", "values": values}])


@pytest.mark.asyncio
async def test_mixed_price_bases_cannot_publish_a_dataset(tmp_path):
    from app.schemas.stock_research_v2 import DatasetBuild
    from app.models.stock_research_dataset import StockResearchDataset
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        async with factory() as session:
            archive = service(session, owner, tmp_path / "artifacts")
            raw = await archive.archive_page(page(), idempotency_key=uuid4(), request_id="a" * 32)
            adjusted = await archive.archive_page(page(price_adjustment="qfq", adjustment_anchor="2026-01-05"), idempotency_key=uuid4(), request_id="b" * 32)
            await session.commit()
            command = DatasetBuild(source_ids=[raw.source_id, adjusted.source_id], start="2026-01-05", end="2026-01-05", warmup_start="2026-01-05", pool_id="qa-only", pool_version="a" * 64, instruments=["000001.SZ"])
            with pytest.raises(JobError) as mixed:
                await archive.build_dataset(command, idempotency_key=uuid4(), request_id="c" * 32)
            assert mixed.value.code == "mixed_adjustment_basis"
            assert await session.scalar(select(func.count()).select_from(StockResearchDataset)) == 0


@pytest.mark.asyncio
async def test_replayed_dataset_rejects_changed_registration_version(tmp_path):
    from app.schemas.stock_research_v2 import DatasetBuild
    from app.models.stock_research_dataset import StockResearchDataset
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        async with factory() as session:
            archive = service(session, owner, tmp_path / "artifacts")
            source = await archive.archive_page(page(), idempotency_key=uuid4(), request_id="a" * 32)
            await session.commit()
            key = uuid4()
            command = DatasetBuild(source_ids=[source.source_id], start="2026-01-05", end="2026-01-05", warmup_start="2026-01-05", pool_id="qa-only", pool_version="a" * 64, instruments=["000001.SZ"])
            saved = await archive.build_dataset(command, idempotency_key=key, request_id="b" * 32)
            await session.commit()
            row = await session.scalar(select(StockResearchDataset).where(StockResearchDataset.id == saved.dataset_id))
            row.data_version = "f" * 64
            await session.commit()
            with pytest.raises(JobError) as conflict:
                await archive.build_dataset(command, idempotency_key=key, request_id="c" * 32)
            assert conflict.value.code == "dataset_integrity_failed"


def test_financial_report_and_adjustment_anchor_cannot_precede_their_own_future():
    with pytest.raises(ValueError):
        page(price_adjustment="qfq", adjustment_anchor="2027-01-01")
    with pytest.raises(ValueError):
        page(kind="financial", frequency="disclosure", financial_basis="consolidated", price_adjustment="not_applicable", units={"roe": "percent"}, observations=[{"instrument_id": "000001.SZ", "effective_date": "2026-01-05", "report_period": "2026-03-31", "announced_at": "2026-01-04T08:00:00Z", "values": {"roe": "10"}}])


def test_numeric_dates_are_not_silently_converted_from_epoch_seconds():
    with pytest.raises(ValueError):
        page(observations=[{"instrument_id": "000001.SZ", "effective_date": 1767571200, "values": {"close": "12", "volume": "0.01"}}])


@pytest.mark.asyncio
async def test_future_collection_cannot_write_and_source_correction_does_not_retarget_saved_dataset(tmp_path):
    from app.schemas.stock_research_v2 import DatasetBuild
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        async with factory() as session:
            archive = service(session, owner, tmp_path / "artifacts")
            with pytest.raises(JobError) as future:
                await archive.archive_page(page(collected_at="2099-01-05T08:00:00Z"), idempotency_key=uuid4(), request_id="a" * 32)
            assert future.value.code == "invalid_source_time"
            assert list((tmp_path / "artifacts").glob("*.json")) == []
            first = await archive.archive_page(page(), idempotency_key=uuid4(), request_id="b" * 32)
            await session.commit()
            build = DatasetBuild(source_ids=[first.source_id], start="2026-01-05", end="2026-01-05", warmup_start="2026-01-05", pool_id="qa-only", pool_version="a" * 64, instruments=["000001.SZ"])
            saved = await archive.build_dataset(build, idempotency_key=uuid4(), request_id="c" * 32)
            await session.commit()
            before = await archive.read_dataset(saved)
            revised = await archive.archive_page(page(collected_at="2026-01-06T08:00:00Z", revises_id=str(first.source_id), observations=[{"instrument_id": "000001.SZ", "effective_date": "2026-01-05", "values": {"close": "13", "volume": "0.02"}}]), idempotency_key=uuid4(), request_id="d" * 32)
            await session.commit()
            assert revised.source_id != first.source_id
            assert await archive.read_dataset(saved) == before


@pytest.mark.asyncio
async def test_owned_archived_financial_source_flows_to_pit_without_losing_vintage_or_precision(tmp_path):
    from datetime import datetime, timezone
    from app.services.stock_research_v2.point_in_time import disclosures_from_source, select_asof
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        async with factory() as session:
            archive = service(session, owner, tmp_path / "artifacts")
            financial = page(kind="financial", financial_basis="consolidated", frequency="disclosure", price_adjustment="not_applicable", units={"roe": "percent"}, observations=[{"instrument_id": "000001.SZ", "effective_date": "2026-01-05", "report_period": "2025-12-31", "announced_at": "2026-01-04T08:00:00Z", "available_at": "2026-01-04T08:00:00Z", "vintage": "original_filing", "vintage_reference": "qa-only-filing-evidence", "values": {"roe": "12.000000000000000001"}}])
            source = await archive.archive_page(financial, idempotency_key=uuid4(), request_id="a" * 32)
            await session.commit()
            owned = await archive.read_source(source.source_id)
            disclosures = disclosures_from_source(owned, source)
            selected = select_asof(disclosures, datetime(2026, 1, 5, 7, 0, tzinfo=timezone.utc))
            assert selected[0].values["roe"] == "12.000000000000000001"
            assert selected[0].source_sha256 == source.artifact.sha256
            with pytest.raises(ValueError, match="指纹"):
                disclosures_from_source(owned, source.model_copy(update={"artifact": source.artifact.model_copy(update={"sha256": "f" * 64})}))
