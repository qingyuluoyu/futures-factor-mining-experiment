"""Carry authorized study inputs into a bounded research child, never a database.

A dispatched research kernel runs in a subprocess that has no engine, no network and
no model credential, yet it still needs the point-in-time study grid — trading
calendar, historical universe and the columnar receipt — that ``load_study_inputs``
builds parent-side.  This module is the only bridge across that boundary, and it is
deliberately one-way and content-addressed:

* the **parent** materializes what it already proved and stores each grid under its
  own digest, so the child reads bytes, never a reference it could reinterpret;
* the child re-derives nothing about identity — the claim it was handed still names
  the job, the scope and the dataset, and this payload only supplies values;
* the payload records the study's own metadata verbatim, so a report can state
  exactly which evidence it was computed from.

Nothing here certifies data, upgrades a qualification or starts a worker.  It moves
numbers across a process boundary and refuses to move anything it cannot bound.
"""
from dataclasses import dataclass
import io
import logging
from collections.abc import Mapping

import numpy as np
from pandas import DataFrame, Index
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from app.schemas.stock_research_v2 import SHA256
from app.services.stock_research_jobs import JobError
from app.services.stock_research_v2.artifacts import ArtifactError, ArtifactStore

logger = logging.getLogger("qihuo.stock_research_v2_transport")

TRANSPORT_VERSION = "stock-v2-study-transport-v1"
# One field grid is bounded on its own: ``load_study_inputs`` already caps the whole
# study at 2,000,000 eligible cells, and this is the per-payload ceiling on top.
PANEL_LIMIT = 16 * 1024 * 1024
INDEX_LIMIT = 1 * 1024 * 1024


@dataclass(frozen=True)
class StudyPayload:
    """What a child needs to rebuild a study grid, and nothing else."""

    fields: dict
    eligible: DataFrame
    metadata: dict


class PanelRef(BaseModel):
    """One stored grid: its digest, its shape and its labelled axes."""

    model_config = ConfigDict(frozen=True)
    sha256: str = Field(pattern=SHA256)
    byte_size: int = Field(gt=0, le=PANEL_LIMIT)
    rows: int = Field(gt=0)
    columns: int = Field(gt=0)
    dates: list[str] = Field(min_length=1)
    instruments: list[str] = Field(min_length=1)


class StudyRef(BaseModel):
    """The whole transported study: one ref per field plus the eligibility mask."""

    model_config = ConfigDict(frozen=True)
    transport_version: str = TRANSPORT_VERSION
    fields: dict[str, PanelRef]
    eligible: PanelRef
    metadata: dict


_STUDY_ADAPTER = TypeAdapter(StudyRef)


def _axis(values) -> list[str]:
    if not isinstance(values, Index):
        raise JobError("study_axis_invalid", "研究面板轴不是明确的索引", 422)
    labels = [str(value) for value in values]
    if not labels or any(not label.strip() for label in labels):
        raise JobError("study_axis_invalid", "研究面板轴存在空标签", 422)
    if len(set(labels)) != len(labels):
        raise JobError("study_axis_ambiguous", "研究面板轴有重复标签，无法无损还原", 422)
    return labels


def _encode_panel(frame, store: ArtifactStore, limit: int) -> PanelRef:
    if not isinstance(frame, DataFrame) or frame.empty:
        raise JobError("study_panel_invalid", "研究面板为空或不是数据框", 422)
    dates, instruments = _axis(frame.index), _axis(frame.columns)
    # float64 is the source precision.  A study grid is real-valued and is NaN
    # wherever a disclosure is missing, so nothing is rounded or filled here: JSON
    # could not carry it (``allow_nan=False``) and decimal text would not be exact.
    try:
        values = frame.to_numpy(dtype="float64", copy=True)
    except (TypeError, ValueError):
        raise JobError("study_panel_invalid", "研究面板含有无法表示为数值的内容", 422) from None
    if values.shape != (len(dates), len(instruments)):
        raise JobError("study_panel_invalid", "研究面板形状与轴标签不一致", 422)
    buffer = io.BytesIO()
    np.save(buffer, values, allow_pickle=False)
    try:
        stored = store.put_binary(buffer.getvalue(), limit=limit)
    except ArtifactError as error:
        # A refusal from the store is still a refusal of *this* panel, and it has to
        # reach the caller under a name it can act on rather than as a bare ValueError.
        raise JobError("study_panel_invalid", f"研究面板无法有界存储：{error}", 422) from None
    return PanelRef(sha256=stored.sha256, byte_size=stored.byte_size, rows=len(dates), columns=len(instruments),
        dates=dates, instruments=instruments)


def _decode_panel(reference: PanelRef, store: ArtifactStore) -> DataFrame:
    try:
        raw = store.read_binary(reference.sha256, limit=PANEL_LIMIT)
    except ArtifactError as error:
        raise JobError("study_panel_invalid", f"研究面板内容与登记指纹不一致：{error}", 422) from None
    try:
        values = np.load(io.BytesIO(raw), allow_pickle=False)
    except (ValueError, OSError):
        raise JobError("study_panel_invalid", "研究面板内容无法解析", 422) from None
    if values.shape != (reference.rows, reference.columns):
        raise JobError("study_panel_invalid", "研究面板形状与登记不一致", 422)
    # The axes are rebuilt from the reference, never from the payload, so editing the
    # stored bytes cannot rename an instrument or move a date.
    return DataFrame(values, index=Index(reference.dates), columns=Index(reference.instruments))


def materialize(payload: StudyPayload, store: ArtifactStore) -> StudyRef:
    """Store a parent-built study grid and return the reference a child can read."""
    if not isinstance(store, ArtifactStore) or not store.root.is_dir():
        raise JobError("study_transport_unconfigured", "研究输入传输存储未就绪", 503)
    if not isinstance(payload, StudyPayload) or not isinstance(payload.metadata, dict):
        raise JobError("study_payload_invalid", "研究输入结构无效", 422)
    if not payload.fields:
        raise JobError("study_payload_invalid", "研究输入没有字段面板", 422)
    fields = {name: _encode_panel(frame, store, PANEL_LIMIT) for name, frame in sorted(payload.fields.items())}
    eligible = _encode_panel(payload.eligible, store, PANEL_LIMIT)
    reference = StudyRef(fields=fields, eligible=eligible, metadata=dict(payload.metadata))
    logger.info("operation=study_inputs_materialized fields=%s cells=%s bytes=%s model_calls=0",
        len(fields), eligible.rows * eligible.columns,
        sum(item.byte_size for item in fields.values()) + eligible.byte_size)
    return reference


def restore(reference, store: ArtifactStore) -> StudyPayload:
    """Rebuild the study grid a child needs from a reference it was handed."""
    if not isinstance(store, ArtifactStore) or not store.root.is_dir():
        raise JobError("study_transport_unconfigured", "研究输入传输存储未就绪", 503)
    # Validate the *wire* form, never a caller's object: pydantic skips revalidation
    # of an already-built model, so handing ``restore`` a StudyRef would let a wrong
    # transport_version or an out-of-range shape through untouched.  Anything that is
    # neither the parsed wire dict nor a StudyRef is refused here rather than coerced.
    if isinstance(reference, Mapping):
        wire = dict(reference)
    elif isinstance(reference, StudyRef):
        wire = _STUDY_ADAPTER.dump_python(reference, mode="json")
    else:
        raise JobError("study_reference_invalid", "研究输入引用无效", 422)
    try:
        validated = _STUDY_ADAPTER.validate_python(wire)
    except ValidationError:
        raise JobError("study_reference_invalid", "研究输入引用无效", 422) from None
    if validated.transport_version != TRANSPORT_VERSION:
        raise JobError("study_reference_invalid", "研究输入传输版本不一致", 422)
    fields = {name: _decode_panel(panel, store) for name, panel in sorted(validated.fields.items())}
    eligible = _decode_panel(validated.eligible, store)
    logger.info("operation=study_inputs_restored fields=%s cells=%s model_calls=0",
        len(fields), validated.eligible.rows * validated.eligible.columns)
    return StudyPayload(fields=fields, eligible=eligible, metadata=dict(validated.metadata))


def reference_digest(reference) -> str:
    """A stable digest of a reference's canonical wire form, to bind a report to its inputs."""
    from app.services.stock_research_v2.archive import descriptor
    wire = _STUDY_ADAPTER.dump_python(reference, mode="json") if isinstance(reference, StudyRef) else reference
    if not isinstance(wire, Mapping):
        raise JobError("study_reference_invalid", "研究输入引用无效", 422)
    # ``descriptor`` sorts keys, so the digest is the same whether the reference is the
    # object the parent built or the bytes the child read back off the wire.
    digest, _ = descriptor(dict(wire), INDEX_LIMIT)
    return digest


__all__ = ["INDEX_LIMIT", "PANEL_LIMIT", "TRANSPORT_VERSION", "PanelRef", "StudyPayload", "StudyRef",
    "materialize", "reference_digest", "restore"]
