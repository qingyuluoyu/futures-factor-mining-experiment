import hashlib
import json
import logging
from pathlib import Path
import re
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import func, select, text, true

from app.models.stock_research_dataset import DatasetBase, DatasetSchemaVersion, StockResearchArtifact, StockResearchDataset, StockResearchDatasetSource, StockResearchSource
from app.schemas.stock_research_v2 import ArtifactRef, DatasetBuild, DatasetRef, Scope, SourcePage, SourceRef
from app.services.stock_research_jobs import JobError, iso_datetime, utc_now
from app.services.stock_research_v2.artifacts import ArtifactError, ArtifactStore

SCHEMA_VERSION = "20261023_015_stock_research_datasets"
SCHEMA_PATH = Path(__file__).resolve().parents[3] / "migrations" / f"{SCHEMA_VERSION}.sql"
MANIFEST_VERSION = "stock-observation-manifest-v1"
MAX_DATASET_BYTES = 64 * 1024 * 1024
MAX_DATASET_ROWS = 1_000_000
logger = logging.getLogger("qihuo.stock_research_archive_v2")


async def datasets_ready(session):
    names = [table.name for table in DatasetBase.metadata.sorted_tables]
    if session.get_bind().dialect.name == "postgresql":
        for name in names:
            if await session.scalar(text("SELECT to_regclass(quote_ident(current_schema()) || '.' || :name)"), {"name": name}) is None:
                return False
    else:
        literals = ",".join("'" + name + "'" for name in names)
        count = await session.scalar(text(f"SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ({literals})"))
        if count != len(names):
            return False
    checksum = await session.scalar(select(DatasetSchemaVersion.checksum).where(DatasetSchemaVersion.version == SCHEMA_VERSION))
    if checksum != hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest():
        return False
    for model in (StockResearchArtifact, StockResearchSource, StockResearchDataset, StockResearchDatasetSource):
        await session.execute(select(model).limit(0))
    return True


def descriptor(payload, maximum):
    digest, size = hashlib.sha256(), 0
    try:
        for part in json.JSONEncoder(sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).iterencode(payload):
            encoded = part.encode("utf8")
            size += len(encoded)
            if size > maximum:
                raise ValueError("size")
            digest.update(encoded)
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise JobError("artifact_payload_invalid", "产物不是大小预算内的规范JSON", 422) from None
    return digest.hexdigest(), size


class ArchiveService:
    def __init__(self, session, scope: Scope, store: ArtifactStore | None = None):
        self.session, self.scope, self.store = session, Scope.model_validate(scope.model_dump()), store

    def require_store(self):
        if not isinstance(self.store, ArtifactStore):
            raise JobError("artifact_store_unavailable", "固定产物存储未配置；没有创建目录或使用其他数据替代", 503)
        return self.store

    async def require_ready(self):
        if not await datasets_ready(self.session):
            raise JobError("dataset_schema_unavailable", "股票数据版本登记尚未迁移或校验和不匹配", 503)

    def ownership(self):
        return (StockResearchArtifact.owner_id == self.scope.owner_id, StockResearchArtifact.trading_mode == self.scope.trading_mode)

    async def lock(self, key, request_id):
        if not isinstance(key, UUID) or not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{32}", request_id):
            raise JobError("invalid_write_identity", "幂等键或请求号无效", 422)
        await self.require_ready()
        if self.session.get_bind().dialect.name == "postgresql":
            identifier = int.from_bytes(hashlib.sha256(f"stock-archive:{self.scope.owner_id}:{self.scope.trading_mode}".encode()).digest()[:8], "big", signed=True)
            await self.session.execute(text("SET LOCAL lock_timeout='5s'"))
            await self.session.execute(text("SET LOCAL statement_timeout='30s'"))
            await self.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": identifier})

    def ref(self, row):
        return ArtifactRef(artifact_id=row.id, sha256=row.sha256, byte_size=row.byte_size, relative_path=row.relative_path)

    async def owned_artifact(self, identifier):
        if not isinstance(identifier, UUID):
            raise JobError("invalid_artifact_id", "产物ID无效", 422)
        row = await self.session.scalar(select(StockResearchArtifact).where(StockResearchArtifact.id == identifier, *self.ownership()))
        if row is None:
            raise JobError("artifact_not_found", "已登记产物不存在", 404)
        return row

    def verified_payload(self, row):
        store = self.require_store()
        try:
            reference = self.ref(row)
            if row.state != "ready" or reference.relative_path != reference.sha256 + ".json":
                raise ArtifactError("metadata")
            payload = store.read_json(reference.sha256)
            digest, size = descriptor(payload, store.max_bytes)
            if size != reference.byte_size or digest != reference.sha256:
                raise ArtifactError("metadata")
            return payload
        except (ArtifactError, ValidationError, JobError):
            raise JobError("artifact_integrity_failed", "产物缺失、隔离或元数据与内容校验不一致；未使用替代数据", 409) from None

    async def read_artifact(self, identifier):
        await self.require_ready()
        return self.verified_payload(await self.owned_artifact(identifier))

    async def _register(self, payload, *, purpose, key, request_id):
        store = self.require_store()
        digest, size = descriptor(payload, store.max_bytes)
        request_hash = hashlib.sha256(f"stock-artifact-v1:{purpose}:{digest}:{size}".encode()).hexdigest()
        existing = await self.session.scalar(select(StockResearchArtifact).where(*self.ownership(), StockResearchArtifact.idempotency_key == key))
        if existing is not None:
            if existing.request_hash != request_hash or existing.purpose != purpose:
                raise JobError("idempotency_conflict", "同一幂等键已登记不同用途或内容", 409)
            self.verified_payload(existing)
            logger.info("request_id=%s user_id=%s mode=%s purpose=%s artifact_id=%s stage=idempotent_verified", request_id, self.scope.owner_id, self.scope.trading_mode, purpose, existing.id)
            return existing
        try:
            stored = store.put_json(payload)
        except ArtifactError:
            logger.warning("request_id=%s user_id=%s mode=%s purpose=%s error_type=artifact_write_failed", request_id, self.scope.owner_id, self.scope.trading_mode, purpose)
            raise JobError("artifact_write_failed", "产物写入失败；未返回可用登记引用", 503) from None
        if stored.sha256 != digest or stored.byte_size != size:
            raise JobError("artifact_integrity_failed", "产物写入前后内容不一致", 409)
        row = StockResearchArtifact(id=uuid4(), owner_id=self.scope.owner_id, trading_mode=self.scope.trading_mode, idempotency_key=key, request_hash=request_hash,
            request_id=request_id, sha256=digest, byte_size=size, relative_path=stored.relative_path, purpose=purpose, state="ready")
        self.session.add(row)
        await self.session.flush()
        logger.info("request_id=%s user_id=%s mode=%s purpose=%s artifact_id=%s bytes=%s stage=prepared_not_committed", request_id, self.scope.owner_id, self.scope.trading_mode, purpose, row.id, size)
        return row

    async def source_rows(self, identifiers):
        rows = (await self.session.execute(select(StockResearchSource, StockResearchArtifact).join(StockResearchArtifact,
            StockResearchSource.artifact_id == StockResearchArtifact.id).where(StockResearchSource.id.in_(identifiers), *self.ownership()).order_by(StockResearchSource.id))).all()
        if len(rows) != len(identifiers):
            raise JobError("source_not_found", "固定来源不存在或不属于当前用户/盘", 404)
        if sum(artifact.byte_size for source, artifact in rows) > MAX_DATASET_BYTES or sum(source.row_count for source, artifact in rows) > MAX_DATASET_ROWS:
            raise JobError("dataset_resource_limit", "固定来源超过单数据集64MiB或100万观察行预算，须分区处理", 422)
        return rows

    def verified_source(self, source, artifact):
        try:
            page = SourcePage.model_validate(self.verified_payload(artifact))
            if artifact.purpose != "source" or source.kind != page.kind or source.stream_key != page.stream_key or source.revises_id != page.revises_id or source.row_count != len(page.observations):
                raise ValueError("metadata")
            return page
        except (ValueError, ValidationError) as error:
            if isinstance(error, JobError):
                raise
            raise JobError("source_integrity_failed", "来源结构或修订元数据不一致", 409) from None

    async def read_source(self, identifier):
        await self.require_ready()
        source, artifact = (await self.source_rows([identifier]))[0]
        return self.verified_source(source, artifact)

    async def archive_page(self, page: SourcePage, *, idempotency_key, request_id):
        page = SourcePage.model_validate(page.model_dump())
        if page.collected_at > utc_now():
            raise JobError("invalid_source_time", "采集时间晚于服务器当前时刻，未伪造或回填时间", 422)
        await self.lock(idempotency_key, request_id)
        if page.revises_id is not None:
            source, artifact = (await self.source_rows([page.revises_id]))[0]
            previous = self.verified_source(source, artifact)
            metadata = ("provider", "kind", "stream_key", "price_adjustment", "adjustment_anchor", "units", "frequency", "financial_basis")
            if any(getattr(previous, field) != getattr(page, field) for field in metadata) or page.collected_at <= previous.collected_at:
                raise JobError("invalid_source_revision", "修订来源/流/单位/锚点不一致或采集时间没有前进", 409)
        artifact = await self._register(page.model_dump(mode="json"), purpose="source", key=idempotency_key, request_id=request_id)
        existing = await self.session.scalar(select(StockResearchSource).where(StockResearchSource.artifact_id == artifact.id))
        if existing is None:
            existing = StockResearchSource(id=uuid4(), artifact_id=artifact.id, kind=page.kind, stream_key=page.stream_key, revises_id=page.revises_id, row_count=len(page.observations))
            self.session.add(existing)
            await self.session.flush()
        self.verified_source(existing, artifact)
        return SourceRef(source_id=existing.id, artifact=self.ref(artifact), kind=existing.kind)

    async def build_dataset(self, command: DatasetBuild, *, idempotency_key, request_id):
        command = DatasetBuild.model_validate(command.model_dump())
        await self.lock(idempotency_key, request_id)
        sources = []
        anchors = set()
        for source, artifact in await self.source_rows(command.source_ids):
            page = self.verified_source(source, artifact)
            if page.kind == "daily":
                anchors.add((page.price_adjustment, page.adjustment_anchor))
            sources.append({"source_id": str(source.id), "sha256": artifact.sha256, "byte_size": artifact.byte_size, "row_count": source.row_count, "kind": source.kind})
        if len(anchors) > 1:
            raise JobError("mixed_adjustment_basis", "同一数据集不能混用原价/不同复权锚点", 409)
        payload = {"manifest_version": MANIFEST_VERSION, "qualification": "observation_only", **command.model_dump(mode="json", exclude={"source_ids"}), "sources": sources}
        artifact = await self._register(payload, purpose="dataset", key=idempotency_key, request_id=request_id)
        row = await self.session.scalar(select(StockResearchDataset).where(StockResearchDataset.manifest_artifact_id == artifact.id))
        if row is None:
            row = StockResearchDataset(id=uuid4(), manifest_artifact_id=artifact.id, data_version=artifact.sha256, qualification="observation_only")
            self.session.add(row)
            await self.session.flush()
            for identifier in command.source_ids:
                self.session.add(StockResearchDatasetSource(dataset_id=row.id, source_id=identifier))
            await self.session.flush()
        if row.data_version != artifact.sha256 or row.qualification != "observation_only":
            raise JobError("dataset_integrity_failed", "数据集登记版本或资格与原始清单不一致", 409)
        return DatasetRef(dataset_id=row.id, data_version=row.data_version, qualification=row.qualification)

    async def describe_dataset(self, reference: DatasetRef, *, max_manifest_bytes: int | None = None):
        reference = DatasetRef.model_validate(reference.model_dump())
        await self.require_ready()
        row = await self.session.scalar(select(StockResearchDataset).join(StockResearchArtifact, StockResearchArtifact.id == StockResearchDataset.manifest_artifact_id).where(StockResearchDataset.id == reference.dataset_id, *self.ownership()))
        if row is None:
            raise JobError("dataset_not_found", "固定数据集不存在", 404)
        if row.data_version != reference.data_version or row.qualification != reference.qualification:
            raise JobError("dataset_version_conflict", "数据集版本或资格与固定引用不一致", 409)
        artifact = await self.owned_artifact(row.manifest_artifact_id)
        if max_manifest_bytes is not None and (type(max_manifest_bytes) is not int or not 0 < max_manifest_bytes <= 8 * 1024 * 1024):
            raise JobError("invalid_manifest_budget", "清单读取预算无效", 422)
        if max_manifest_bytes is not None and artifact.byte_size > max_manifest_bytes:
            raise JobError("manifest_requires_background_inspection", "清单超过交互读取预算，须后台检查；未同步展开全部数据", 422)
        manifest = self.verified_payload(artifact)
        if not isinstance(manifest, dict) or artifact.purpose != "dataset" or artifact.sha256 != row.data_version or manifest.get("manifest_version") != MANIFEST_VERSION or manifest.get("qualification") != row.qualification:
            raise JobError("dataset_integrity_failed", "数据集清单与登记不一致", 409)
        try:
            identifiers = [UUID(item["source_id"]) for item in manifest["sources"]]
            if not 1 <= len(identifiers) <= 1000 or len(set(identifiers)) != len(identifiers):
                raise ValueError("sources")
            DatasetBuild.model_validate({**{key: value for key, value in manifest.items() if key not in {"sources", "manifest_version", "qualification"}}, "source_ids": identifiers})
        except (KeyError, TypeError, ValueError):
            raise JobError("dataset_integrity_failed", "数据集来源清单结构无效", 409) from None
        linked = list((await self.session.execute(select(StockResearchDatasetSource.source_id).where(StockResearchDatasetSource.dataset_id == row.id).order_by(StockResearchDatasetSource.source_id))).scalars())
        if sorted(linked, key=str) != sorted(identifiers, key=str):
            raise JobError("dataset_integrity_failed", "数据集关联来源缺失或变化", 409)
        actual = []
        for source, source_artifact in await self.source_rows(identifiers):
            actual.append({"source_id": str(source.id), "sha256": source_artifact.sha256, "byte_size": source_artifact.byte_size, "row_count": source.row_count, "kind": source.kind})
        if actual != manifest["sources"]:
            raise JobError("dataset_integrity_failed", "固定来源版本/大小/行数变化", 409)
        return manifest

    async def read_dataset(self, reference: DatasetRef):
        manifest = await self.describe_dataset(reference)
        for source, artifact in await self.source_rows([UUID(item["source_id"]) for item in manifest["sources"]]):
            self.verified_source(source, artifact)
        return manifest

    async def list_datasets(self, *, limit, offset):
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 100000:
            raise JobError("invalid_pagination", "数据集分页超出范围", 422)
        await self.require_ready()
        scoped = select(StockResearchDataset.id).join(StockResearchArtifact, StockResearchDataset.manifest_artifact_id == StockResearchArtifact.id).where(*self.ownership()).cte("owned_datasets")
        count = select(func.count().label("total")).select_from(scoped).cte("dataset_count")
        page = select(scoped).order_by(scoped.c.id).limit(limit).offset(offset).cte("dataset_page")
        rows = (await self.session.execute(select(StockResearchDataset, StockResearchArtifact.state, count.c.total).select_from(count).outerjoin(page, true()).outerjoin(
            StockResearchDataset, StockResearchDataset.id == page.c.id).outerjoin(StockResearchArtifact, StockResearchArtifact.id == StockResearchDataset.manifest_artifact_id).order_by(page.c.id))).all()
        items = [{"dataset_id": str(row.id), "data_version": row.data_version, "qualification": row.qualification, "artifact_state": state, "integrity": "not_checked", "created_at": iso_datetime(row.created_at)} for row, state, total in rows if row is not None]
        total = rows[0][2]
        return {"items": items, "total": total, "has_more": offset + len(items) < total}
