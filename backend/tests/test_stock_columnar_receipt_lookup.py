"""A research claim carries a DatasetRef, never a columnar receipt.

``ColumnarService.build`` is the only writer and it keys idempotency on the *dataset
job's* key, which a research job does not carry.  These tests pin the read side a
research claim actually needs: the same ``(owner, mode, dataset)`` row, found through
the index that already exists for it — and the refusals when that row is absent,
isolated, foreign, or ambiguous.
"""
from uuid import uuid4

import pytest

from app.services.stock_research_jobs import JobError
from tests.test_stock_columnar_registry import prepared
from tests.test_stock_research_archive_v2 import service
from tests.test_stock_research_panels_v2 import snapshot
from tests.test_stock_research_job_service import job_database


async def built(tmp_path, factory, owner, *, key=None, reference=None):
    """One real columnar registration for the current scope.

    ``reference`` lets a caller build the *same* dataset twice, which is the only way
    to reach the multi-registration path: each ``snapshot`` mints a new parent.
    """
    from app.services.stock_research_v2.columnar import ColumnarService
    from app.services.stock_research_v2.columnar_store import ParquetStore
    async with factory() as session:
        archive = service(session, owner, tmp_path / "source")
        if reference is None:
            reference, _ = await snapshot(archive)
        target = tmp_path / "columnar"
        target.mkdir(exist_ok=True)
        receipt = await ColumnarService(archive, ParquetStore(target)).build(reference, idempotency_key=key or uuid4(), request_id="a" * 32)
        await session.commit()
        return reference, receipt


@pytest.mark.asyncio
async def test_the_receipt_for_a_dataset_is_found_without_the_building_jobs_idempotency_key(tmp_path):
    from app.services.stock_research_v2.columnar import ColumnarService
    from app.services.stock_research_v2.columnar_store import ParquetStore
    async with job_database() as (factory, owner, other):
        await prepared(factory)
        reference, original = await built(tmp_path, factory, owner, key=uuid4())
        # A research job knows only the dataset.  Nothing here may build, rewrite or
        # re-collect: the receipt already exists and is read back through the index.
        async with factory() as session:
            found = await ColumnarService(service(session, owner, tmp_path / "source"), ParquetStore(tmp_path / "columnar")).receipt_for(reference)
        assert found == original
        # And it is a usable receipt, not just an equal-looking object.
        async with factory() as session:
            view = await ColumnarService(service(session, owner, tmp_path / "source"), ParquetStore(tmp_path / "columnar")).read(found)
            assert view.manifest.dataset == reference


@pytest.mark.asyncio
async def test_a_dataset_with_no_columnar_registration_is_refused_by_name(tmp_path):
    from app.schemas.stock_research_v2 import DatasetRef
    from app.services.stock_research_v2.columnar import ColumnarService
    from app.services.stock_research_v2.columnar_store import ParquetStore
    async with job_database() as (factory, owner, other):
        await prepared(factory)
        (tmp_path / "columnar").mkdir()
        async with factory() as session:
            reference = DatasetRef(dataset_id=uuid4(), data_version="a" * 64, qualification="observation_only")
            with pytest.raises(JobError) as absent:
                await ColumnarService(service(session, owner, tmp_path / "source"), ParquetStore(tmp_path / "columnar")).receipt_for(reference)
        assert absent.value.code == "columnar_not_found" and absent.value.status == 404


@pytest.mark.asyncio
async def test_a_quarantined_registration_is_never_handed_out(tmp_path):
    from app.services.stock_research_v2.columnar import ColumnarService
    from app.services.stock_research_v2.columnar_store import ParquetStore
    async with job_database() as (factory, owner, other):
        await prepared(factory)
        reference, _ = await built(tmp_path, factory, owner)
        async with factory() as session:
            from sqlalchemy import update
            from app.models.stock_columnar import StockColumnarDataset as Row
            await session.execute(update(Row).where(Row.dataset_id == reference.dataset_id).values(state="quarantined"))
            await session.commit()
        async with factory() as session:
            with pytest.raises(JobError) as isolated:
                await ColumnarService(service(session, owner, tmp_path / "source"), ParquetStore(tmp_path / "columnar")).receipt_for(reference)
        assert isolated.value.code == "columnar_integrity_failed" and isolated.value.status == 409


@pytest.mark.asyncio
async def test_a_receipt_is_never_found_through_another_user_or_another_disk(tmp_path):
    from app.services.stock_research_v2.columnar import ColumnarService
    from app.services.stock_research_v2.columnar_store import ParquetStore
    async with job_database() as (factory, owner, other):
        await prepared(factory)
        reference, _ = await built(tmp_path, factory, owner)
        async with factory() as session:
            for archive in (service(session, other, tmp_path / "source"), service(session, owner, tmp_path / "source", mode="live")):
                with pytest.raises(JobError) as denied:
                    await ColumnarService(archive, ParquetStore(tmp_path / "columnar")).receipt_for(reference)
                assert denied.value.status == 404


@pytest.mark.asyncio
async def test_two_registrations_of_one_dataset_that_disagree_are_refused_rather_than_picked(tmp_path):
    """Two builds of one dataset are content-identical, so a disagreement is a real
    registry fault — and silently taking the newer one would hide it."""
    from app.services.stock_research_v2.columnar import ColumnarService
    from app.services.stock_research_v2.columnar_store import ParquetStore
    async with job_database() as (factory, owner, other):
        await prepared(factory)
        reference, first = await built(tmp_path, factory, owner, key=uuid4())
        # A second build under a different idempotency key is the normal case.
        _, second = await built(tmp_path, factory, owner, key=uuid4(), reference=reference)
        assert second.manifest_sha256 == first.manifest_sha256
        async with factory() as session:
            found = await ColumnarService(service(session, owner, tmp_path / "source"), ParquetStore(tmp_path / "columnar")).receipt_for(reference)
            assert found.manifest_sha256 == first.manifest_sha256
        # Now make the second registration claim different content for one dataset.
        async with factory() as session:
            from sqlalchemy import update
            from app.models.stock_columnar import StockColumnarDataset as Row
            await session.execute(update(Row).where(Row.id == second.columnar_id).values(manifest_sha256="b" * 64))
            await session.commit()
        async with factory() as session:
            with pytest.raises(JobError) as conflict:
                await ColumnarService(service(session, owner, tmp_path / "source"), ParquetStore(tmp_path / "columnar")).receipt_for(reference)
        assert conflict.value.code == "columnar_integrity_failed"
