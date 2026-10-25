"""Immutable, source-partitioned Parquet. No daily financial alignment here."""
import hashlib
import os
from pathlib import Path
import re
import tempfile

from app.schemas.stock_columnar import ColumnarPartition, ENGINE_VERSION, FORMAT_VERSION
from app.schemas.stock_research_v2 import KIND_FIELDS, SourcePage
from app.services.stock_research_jobs import JobError


def arrow_engine():
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        raise JobError("columnar_engine_unavailable", "独立股票列式依赖未安装", 503) from None
    if pa.__version__ != ENGINE_VERSION:
        raise JobError("columnar_engine_unavailable", "列式引擎与锁定版本不符", 503)
    return pa, pq


def partition_schema(partition):
    pa, _ = arrow_engine()
    fields = [pa.field("instrument_id", pa.string(), nullable=False), pa.field("effective_date", pa.date32(), nullable=False),
        pa.field("report_period", pa.date32()), pa.field("announced_on", pa.date32()),
        pa.field("announced_at", pa.timestamp("us", tz="UTC")), pa.field("available_at", pa.timestamp("us", tz="UTC")),
        pa.field("vintage", pa.string(), nullable=False), pa.field("vintage_reference", pa.string())]
    for name in sorted(KIND_FIELDS[partition.kind]):
        fields.extend([pa.field(name, pa.string()), pa.field(name + "__present", pa.bool_(), nullable=False)])
    metadata = {"format_version": FORMAT_VERSION, "engine_version": ENGINE_VERSION, "source_id": str(partition.source_id),
        "source_sha256": partition.source_sha256, "data_version": partition.data_version, "kind": partition.kind}
    return pa.schema(fields, metadata={key.encode(): value.encode() for key, value in metadata.items()})


class ParquetStore:
    def __init__(self, root):
        self.root = Path(root).absolute()
        if not self.root.is_dir() or self.root.resolve() != self.root:
            raise JobError("columnar_store_unavailable", "列式根必须已存在且不能为链接", 503)

    def path(self, digest):
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise JobError("columnar_integrity_failed", "列式内容指纹无效", 409)
        target = self.root / (digest + ".parquet")
        if target.is_symlink() or target.resolve().parent != self.root:
            raise JobError("columnar_integrity_failed", "列式路径越界或为链接", 409)
        return target

    @staticmethod
    def fingerprint(stream):
        size, digest = 0, hashlib.sha256()
        while block := stream.read(65536):
            size += len(block)
            if size > 64 * 1024 * 1024:
                raise JobError("columnar_integrity_failed", "列式文件超过预算", 409)
            digest.update(block)
        return digest.hexdigest(), size

    def put_page(self, page, *, source_id, source_sha256, data_version):
        pa, pq = arrow_engine()
        page = SourcePage.model_validate(page.model_dump())
        # Validate all binding inputs before creating a temporary file.
        binding = ColumnarPartition(source_id=source_id, source_sha256=source_sha256, data_version=data_version, kind=page.kind,
            sha256="0" * 64, relative_path="0" * 64 + ".parquet", byte_size=1, rows=len(page.observations))
        schema = partition_schema(binding)
        ordered = sorted(page.observations, key=lambda row: (row.effective_date, row.instrument_id, row.model_dump_json()))
        with tempfile.NamedTemporaryFile(dir=self.root, suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
        try:
            with pq.ParquetWriter(temporary, schema, version="2.6", compression="zstd", compression_level=3,
                    use_dictionary=False, write_page_checksum=True) as writer:
                for offset in range(0, len(ordered), 1024):
                    rows = []
                    for observation in ordered[offset:offset + 1024]:
                        row = observation.model_dump(exclude={"values"})
                        for field in KIND_FIELDS[page.kind]:
                            row[field] = observation.values.get(field)
                            row[field + "__present"] = field in observation.values
                        rows.append(row)
                    writer.write_table(pa.Table.from_pylist(rows, schema=schema), row_group_size=1024)
            with temporary.open("rb+") as stream:
                os.fsync(stream.fileno())
                digest, size = self.fingerprint(stream)
            result = ColumnarPartition(**{**binding.model_dump(), "sha256": digest, "relative_path": digest + ".parquet", "byte_size": size})
            try:
                os.link(temporary, self.path(digest))
            except FileExistsError:
                pass
            # No receipt is returned for an existing corrupted object.
            for _ in self.iter_batches(result, fields=sorted(KIND_FIELDS[page.kind])):
                pass
            return result
        except JobError:
            raise
        except Exception:
            raise JobError("columnar_write_failed", "列式写入或核验失败，未返回可用引用", 409) from None
        finally:
            temporary.unlink(missing_ok=True)

    def iter_batches(self, partition, *, fields, batch_rows=1024):
        partition = ColumnarPartition.model_validate(partition.model_dump())
        if not isinstance(fields, list) or not fields or any(not isinstance(field, str) for field in fields) or len(fields) != len(set(fields)) or not set(fields) <= KIND_FIELDS[partition.kind]:
            raise JobError("invalid_columnar_projection", "列式读取字段无效或不属于所选数据类别", 422)
        if type(batch_rows) is not int or not 1 <= batch_rows <= 2048:
            raise JobError("invalid_columnar_budget", "列式批大小必须为1至2048", 422)
        _, pq = arrow_engine()
        expected = partition_schema(partition)
        columns = [name for name in expected.names if name not in KIND_FIELDS[partition.kind] and not name.endswith("__present")]
        for field in sorted(fields):
            columns.extend([field, field + "__present"])
        try:
            with self.path(partition.sha256).open("rb") as source:
                digest, size = self.fingerprint(source)
                if digest != partition.sha256 or size != partition.byte_size:
                    raise ValueError("content mismatch")
                source.seek(0)
                with pq.ParquetFile(source, pre_buffer=False, thrift_string_size_limit=1024 * 1024,
                        thrift_container_size_limit=100000, page_checksum_verification=True, arrow_extensions_enabled=False) as file:
                    if file.metadata.num_rows != partition.rows or not file.schema_arrow.equals(expected, check_metadata=True):
                        raise ValueError("schema mismatch")
                    for batch in file.iter_batches(batch_size=batch_rows, columns=columns, use_threads=False):
                        yield batch.to_pylist()
        except JobError:
            raise
        except Exception:
            raise JobError("columnar_integrity_failed", "列式文件缺失或内容/字段/绑定核验失败", 409) from None
