import hashlib
from uuid import uuid4

import pytest

from app.services.stock_research_jobs import JobError
from tests.test_stock_research_archive_v2 import service
from tests.test_stock_research_job_service import job_database
from tests.test_stock_research_panels_v2 import snapshot


async def prepared(factory, *, parent_ready=False):
    from app.models.stock_columnar import ColumnarBase, ColumnarSchemaVersion
    from app.services.stock_research_v2.columnar import SCHEMA_PATH, SCHEMA_VERSION
    from tests.test_stock_research_archive_v2 import prepare
    if not parent_ready:
        await prepare(factory)
    async with factory() as session:
        ColumnarBase.metadata.create_all(session.get_bind())
        session.add(ColumnarSchemaVersion(version=SCHEMA_VERSION, checksum=hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()))
        await session.commit()


@pytest.mark.asyncio
async def test_scoped_registration_idempotency_and_actual_parquet_reading(tmp_path):
    from app.services.stock_research_v2.columnar import ColumnarService
    from app.services.stock_research_v2.columnar_store import ParquetStore
    async with job_database() as (factory, owner, other):
        await prepared(factory)
        async with factory() as session:
            archive = service(session, owner, tmp_path / "source")
            ref, source = await snapshot(archive)
            cache_root = tmp_path / "columnar"
            cache_root.mkdir()
            store = ParquetStore(cache_root)
            registry = ColumnarService(archive, store)
            key = uuid4()
            first = await registry.build(ref, idempotency_key=key, request_id="a" * 32)
            assert await registry.build(ref, idempotency_key=key, request_id="b" * 32) == first
            await session.commit()
        async with factory() as session:
            archive = service(session, owner, tmp_path / "source")
            view = await ColumnarService(archive, store).read(first)
            batches = list(view.batches(fields=["close", "volume"], batch_rows=2))
            assert view.manifest.dataset == ref and view.manifest.representation == "source_observations_not_aligned"
            rows = [row for batch in batches for row in batch["rows"]]
            assert rows[0]["close"] == "12.000000000000000001" and rows[0]["volume"] == "0.01"
            assert all(len(batch["rows"]) <= 2 and batch["kind"] == "daily" for batch in batches)
            metadata = batches[0]["metadata"]
            assert metadata["provider"] == "teajoin" and metadata["market"] == "CN_A"
            assert metadata["currency"] == "CNY" and metadata["timezone"] == "Asia/Shanghai"
            assert metadata["units"]["volume"] == "share" and metadata["price_adjustment"] == "raw"
            assert metadata["collected_at"] == "2026-01-06T08:00:00Z"
            assert metadata["calendar_reference"] == "unverified"
            assert "observations" not in metadata
            metadata["units"]["volume"] = "corrupted-consumer-unit"
            assert next(view.batches(fields=["volume"]))["metadata"]["units"]["volume"] == "share"
            for fields in [["roe"], ["close", "roe"]]:
                with pytest.raises(JobError) as missing:
                    list(view.batches(fields=fields))
                assert missing.value.code == "columnar_fields_unavailable"
            for scope_archive in [service(session, other, tmp_path / "source"), service(session, owner, tmp_path / "source", mode="live")]:
                with pytest.raises(JobError) as denied:
                    await ColumnarService(scope_archive, store).read(first)
                assert denied.value.status == 404


@pytest.mark.asyncio
async def test_columnar_projection_rejects_absent_data_kind_instead_of_empty_success(tmp_path):
    from app.services.stock_research_v2.columnar import ColumnarService
    from app.services.stock_research_v2.columnar_store import ParquetStore
    async with job_database() as (factory, owner, other):
        await prepared(factory)
        async with factory() as session:
            archive = service(session, owner, tmp_path / "source")
            ref, _ = await snapshot(archive)
            target = tmp_path / "columnar"; target.mkdir()
            registry = ColumnarService(archive, ParquetStore(target))
            receipt = await registry.build(ref, idempotency_key=uuid4(), request_id="a" * 32)
            view = await registry.read(receipt)
            for fields in [["roe"], ["close", "roe"]]:
                with pytest.raises(JobError) as missing:
                    list(view.batches(fields=fields))
                assert missing.value.code == "columnar_fields_unavailable"


@pytest.mark.asyncio
async def test_rollback_leaves_no_authorized_receipt_and_retry_reuses_immutable_parquet(tmp_path):
    from app.services.stock_research_v2.columnar import ColumnarService
    from app.services.stock_research_v2.columnar_store import ParquetStore
    async with job_database() as (factory, owner, other):
        await prepared(factory)
        target = tmp_path / "columnar"; target.mkdir()
        store = ParquetStore(target)
        key = uuid4()
        async with factory() as session:
            archive = service(session, owner, tmp_path / "source")
            ref, _ = await snapshot(archive)
            registry = ColumnarService(archive, store)
            first = await registry.build(ref, idempotency_key=key, request_id="a" * 32)
            await session.rollback()
        content_before = {path.name: path.read_bytes() for path in target.glob("*.parquet")}
        assert len(content_before) == 1
        async with factory() as session:
            registry = ColumnarService(service(session, owner, tmp_path / "source"), store)
            with pytest.raises(JobError) as unregistered:
                await registry.read(first)
            assert unregistered.value.status == 404
            second = await registry.build(ref, idempotency_key=key, request_id="b" * 32)
            assert second.columnar_id != first.columnar_id
            await session.commit()
        assert {path.name: path.read_bytes() for path in target.glob("*.parquet")} == content_before


@pytest.mark.asyncio
async def test_same_columnar_idempotency_key_cannot_be_rebound_to_a_changed_dataset(tmp_path):
    from app.services.stock_research_v2.columnar import ColumnarService
    from app.services.stock_research_v2.columnar_store import ParquetStore
    async with job_database() as (factory, owner, other):
        await prepared(factory)
        async with factory() as session:
            archive = service(session, owner, tmp_path / "source")
            ref, _ = await snapshot(archive)
            target = tmp_path / "columnar"; target.mkdir()
            registry = ColumnarService(archive, ParquetStore(target))
            key = uuid4()
            receipt = await registry.build(ref, idempotency_key=key, request_id="a" * 32)
            before = {path.name: path.read_bytes() for path in target.glob("*.parquet")}
            changed = ref.model_copy(update={"data_version": "c" * 64})
            with pytest.raises(JobError) as conflict:
                await registry.build(changed, idempotency_key=key, request_id="b" * 32)
            assert conflict.value.code == "idempotency_conflict"
            assert (await registry.read(receipt)).manifest.dataset == ref
            assert {path.name: path.read_bytes() for path in target.glob("*.parquet")} == before


@pytest.mark.asyncio
async def test_changed_parquet_or_parent_source_is_not_silently_rebuilt(tmp_path):
    from app.services.stock_research_v2.columnar import ColumnarService
    from app.services.stock_research_v2.columnar_store import ParquetStore
    async with job_database() as (factory, owner, other):
        await prepared(factory)
        async with factory() as session:
            archive = service(session, owner, tmp_path / "source")
            ref, source = await snapshot(archive)
            target = tmp_path / "columnar"
            target.mkdir()
            registry = ColumnarService(archive, ParquetStore(target))
            key = uuid4()
            receipt = await registry.build(ref, idempotency_key=key, request_id="a" * 32)
            await session.commit()
            (tmp_path / "source" / source.artifact.relative_path).write_bytes(b"bad-source")
            with pytest.raises(JobError):
                await registry.read(receipt)
        async with factory() as session:
            archive = service(session, owner, tmp_path / "another-source")
            ref, _ = await snapshot(archive)
            registry = ColumnarService(archive, ParquetStore(target))
            key = uuid4()
            receipt = await registry.build(ref, idempotency_key=key, request_id="b" * 32)
            view = await registry.read(receipt)
            path = target / view.manifest.partitions[0].relative_path
            path.write_bytes(b"bad-columnar")
            for operation in [registry.read(receipt), registry.build(ref, idempotency_key=key, request_id="c" * 32)]:
                with pytest.raises(JobError) as error:
                    await operation
                assert error.value.status == 409
            assert path.read_bytes() == b"bad-columnar"


@pytest.mark.asyncio
async def test_unmigrated_or_unauthorized_build_writes_no_partition(tmp_path):
    from app.services.stock_research_v2.columnar import ColumnarService
    from app.services.stock_research_v2.columnar_store import ParquetStore
    from tests.test_stock_research_archive_v2 import prepare
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        async with factory() as session:
            archive = service(session, owner, tmp_path / "source")
            ref, _ = await snapshot(archive)
            target = tmp_path / "columnar"
            target.mkdir()
            with pytest.raises(JobError) as unmigrated:
                await ColumnarService(archive, ParquetStore(target)).build(ref, idempotency_key=uuid4(), request_id="a" * 32)
            assert unmigrated.value.status == 503 and not list(target.iterdir())
        await prepared(factory, parent_ready=True)
        async with factory() as session:
            archive = service(session, other, tmp_path / "source")
            with pytest.raises(JobError) as denied:
                await ColumnarService(archive, ParquetStore(target)).build(ref, idempotency_key=uuid4(), request_id="a" * 32)
            assert denied.value.status == 404 and not list(target.iterdir())
