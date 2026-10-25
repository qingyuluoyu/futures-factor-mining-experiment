from dataclasses import dataclass
from copy import deepcopy
import hashlib
import logging
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import select, text

from app.models.stock_columnar import ColumnarBase, ColumnarSchemaVersion, StockColumnarDataset as Row
from app.schemas.stock_columnar import ColumnarManifest, ColumnarRef, ENGINE_VERSION, FORMAT_VERSION
from app.schemas.stock_research_v2 import DatasetBuild, DatasetRef, FIELD_UNITS, KIND_FIELDS
from app.services.stock_research_jobs import JobError
from app.services.stock_research_v2.archive import descriptor
from app.services.stock_research_v2.columnar_store import ParquetStore, arrow_engine

SCHEMA_VERSION = "20261024_018_stock_columnar_datasets"
SCHEMA_PATH = Path(__file__).resolve().parents[3] / "migrations" / f"{SCHEMA_VERSION}.sql"
logger = logging.getLogger("qihuo.stock_columnar")


async def columnar_ready(session):
    names = [table.name for table in ColumnarBase.metadata.sorted_tables]
    if session.get_bind().dialect.name == "postgresql":
        for name in names:
            if await session.scalar(text("SELECT to_regclass(quote_ident(current_schema()) || '.' || :name)"), {"name": name}) is None:
                return False
    else:
        literals = ",".join("'" + name + "'" for name in names)
        if await session.scalar(text(f"SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ({literals})")) != len(names):
            return False
    if await session.scalar(select(ColumnarSchemaVersion.checksum).where(ColumnarSchemaVersion.version == SCHEMA_VERSION)) != hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest():
        return False
    await session.execute(select(Row).limit(0))
    return True


def selection_for(parent):
    return DatasetBuild(**{key: parent[key] for key in ("start", "end", "warmup_start", "pool_id", "pool_version", "instruments")},
        source_ids=[UUID(item["source_id"]) for item in parent["sources"]])


@dataclass(frozen=True)
class ColumnarView:
    manifest: ColumnarManifest
    store: ParquetStore
    source_metadata: dict

    def batches(self, *, fields, batch_rows=1024):
        if not isinstance(fields, list) or not fields or any(not isinstance(field, str) for field in fields) or len(fields) != len(set(fields)) or not set(fields) <= set(FIELD_UNITS):
            raise JobError("invalid_columnar_projection", "观察读取字段无效", 422)
        if type(batch_rows) is not int or not 1 <= batch_rows <= 2048:
            raise JobError("invalid_columnar_budget", "观察读取批大小无效", 422)
        available = set().union(*(KIND_FIELDS[item.kind] for item in self.manifest.partitions))
        if not set(fields) <= available:
            raise JobError("columnar_fields_unavailable", "固定列式数据集缺少所请求字段的数据类别；未返回空成功或补值", 422)
        selection = self.manifest.selection
        selected = set(selection.instruments)
        for partition in self.manifest.partitions:
            requested = sorted(set(fields) & KIND_FIELDS[partition.kind])
            if not requested:
                continue
            for batch in self.store.iter_batches(partition, fields=requested, batch_rows=batch_rows):
                bounded = [row for row in batch if row["instrument_id"] in selected and selection.warmup_start <= row["effective_date"] <= selection.end]
                if bounded:
                    yield {"source_id": str(partition.source_id), "kind": partition.kind,
                        "metadata": deepcopy(self.source_metadata[str(partition.source_id)]), "rows": bounded}


class ColumnarService:
    def __init__(self, archive, store: ParquetStore):
        self.archive, self.store, self.session, self.scope = archive, store, archive.session, archive.scope

    async def require_ready(self):
        if not await columnar_ready(self.session):
            raise JobError("columnar_schema_unavailable", "列式登记尚未显式迁移和验证", 503)
        if not isinstance(self.store, ParquetStore) or not self.store.root.is_dir():
            raise JobError("columnar_store_unavailable", "列式根尚未就绪", 503)
        arrow_engine()

    def ownership(self):
        return (Row.owner_id == self.scope.owner_id, Row.trading_mode == self.scope.trading_mode)

    async def build(self, reference, *, idempotency_key, request_id):
        reference = DatasetRef.model_validate(reference.model_dump())
        await self.require_ready()
        await self.archive.lock(idempotency_key, request_id)
        input_hash, _ = descriptor({"dataset": reference.model_dump(mode="json"), "format_version": FORMAT_VERSION, "engine_version": ENGINE_VERSION}, 4096)
        previous = await self.session.scalar(select(Row).where(*self.ownership(), Row.idempotency_key == idempotency_key))
        if previous is not None:
            if previous.input_hash != input_hash:
                raise JobError("idempotency_conflict", "同一列式幂等键已绑定不同数据版本", 409)
            receipt = ColumnarRef(columnar_id=previous.id, dataset=reference, manifest_sha256=previous.manifest_sha256)
            await self.read(receipt)
            return receipt
        parent = await self.archive.read_dataset(reference)
        partitions = []
        total_bytes = 0
        for source, artifact in await self.archive.source_rows([UUID(item["source_id"]) for item in parent["sources"]]):
            page = self.archive.verified_source(source, artifact)
            partition = self.store.put_page(page, source_id=source.id, source_sha256=artifact.sha256, data_version=reference.data_version)
            partitions.append(partition)
            total_bytes += partition.byte_size
            if total_bytes > 64 * 1024 * 1024:
                raise JobError("columnar_budget_exceeded", "列式产物总量超出64MiB预算", 422)
        manifest = ColumnarManifest(dataset=reference, selection=selection_for(parent), partitions=sorted(partitions, key=lambda item: str(item.source_id)))
        payload = manifest.model_dump(mode="json")
        digest, _ = descriptor(payload, 512 * 1024)
        row = Row(id=uuid4(), owner_id=self.scope.owner_id, trading_mode=self.scope.trading_mode, dataset_id=reference.dataset_id,
            idempotency_key=idempotency_key, input_hash=input_hash, manifest_sha256=digest, manifest_json=payload, request_id=request_id, state="ready")
        self.session.add(row)
        await self.session.flush()
        logger.info("request_id=%s user_id=%s mode=%s operation=columnar_prepared_not_committed data_version=%s partitions=%s", request_id, self.scope.owner_id, self.scope.trading_mode, reference.data_version, len(partitions))
        return ColumnarRef(columnar_id=row.id, dataset=reference, manifest_sha256=digest)

    async def read(self, receipt, *, expected_idempotency_key=None):
        receipt = ColumnarRef.model_validate(receipt.model_dump())
        if expected_idempotency_key is not None and not isinstance(expected_idempotency_key, UUID):
            raise JobError("invalid_idempotency_key", "列式任务绑定必须为明确UUID", 422)
        await self.require_ready()
        row = await self.session.scalar(select(Row).where(Row.id == receipt.columnar_id, *self.ownership()).execution_options(populate_existing=True))
        if row is None:
            raise JobError("columnar_not_found", "列式登记不存在", 404)
        if expected_idempotency_key is not None and row.idempotency_key != expected_idempotency_key:
            raise JobError("job_result_mismatch", "列式登记未绑定本任务的幂等身份", 409)
        try:
            manifest = ColumnarManifest.model_validate(row.manifest_json)
            digest, _ = descriptor(manifest.model_dump(mode="json"), 512 * 1024)
            if row.state != "ready" or row.dataset_id != receipt.dataset.dataset_id or manifest.dataset != receipt.dataset or digest != row.manifest_sha256 or digest != receipt.manifest_sha256:
                raise ValueError("registry binding mismatch")
        except (ValueError, ValidationError):
            raise JobError("columnar_integrity_failed", "列式登记已隔离或版本/指纹无效", 409) from None
        parent = await self.archive.read_dataset(receipt.dataset)
        if manifest.selection != selection_for(parent):
            raise JobError("columnar_integrity_failed", "列式选择范围与固定父版本不一致", 409)
        expected = {item["source_id"]: item for item in parent["sources"]}
        source_metadata = {}
        for source, artifact in await self.archive.source_rows([UUID(identifier) for identifier in expected]):
            page = self.archive.verified_source(source, artifact)
            source_metadata[str(source.id)] = page.model_dump(mode="json", exclude={"observations"})
        for partition in manifest.partitions:
            source = expected.get(str(partition.source_id))
            if source is None or (partition.source_sha256, partition.rows, partition.kind) != (source["sha256"], source["row_count"], source["kind"]):
                raise JobError("columnar_integrity_failed", "列式来源绑定不一致", 409)
            # Validate every partition before returning a usable view; no repair.
            for _ in self.store.iter_batches(partition, fields=sorted(KIND_FIELDS[partition.kind])):
                pass
        return ColumnarView(manifest, self.store, source_metadata)
