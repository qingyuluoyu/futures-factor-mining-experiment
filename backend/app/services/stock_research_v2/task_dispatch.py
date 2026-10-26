"""Closed registry that maps a persisted v2 job kind to its own kernel and module.

Dispatch reads the persisted ``job.kind`` and nothing else: no request body, no
parameter field and no fallback ever selects a kernel.  A kind that is not
registered here is refused by name (``task_kind_unregistered``) so the job fails
loudly, and the observation ``dataset`` kind is deliberately absent — it keeps its
own module and its own publication path, so no research kernel can be reached by
renaming a dataset job and no dataset job can be reached by naming a research one.

The registry is also the single place that knows which of the three mutually
exclusive result shapes migration 021 allows a kind to publish, and whether 021 is
actually applied on the live engine (``chain_ready``), so a research claim is never
handed out against a schema that cannot store its result.
"""
import hashlib
import logging
import re
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.models.stock_ml_registry import StockMLExperiment
from app.models.stock_research_v2_job import StockResearchV2Job
from app.schemas.stock_research_v2 import SHA256, Contract, DatasetRef, Scope
from app.services.stock_research_jobs import JobError
from app.services.stock_research_v2.archive import ArchiveService
from app.services.stock_research_v2.qualification import allows

logger = logging.getLogger("qihuo.stock_research_v2_dispatch")

CHAIN_VERSION = "20261025_021_stock_research_v2_task_chain"
CHAIN_PATH = Path(__file__).resolve().parents[3] / "migrations" / f"{CHAIN_VERSION}.sql"
CHAIN_TABLE = "stock_research_v2_task_chain_schema_versions"
# The three columns 021 adds, whose combinations its relaxed result CHECK restricts.
CHAIN_COLUMNS = ("result_experiment_id", "result_report_sha256", "result_artifact_id")

RESEARCH_MODULE = "app.services.stock_research_v2.research_process"
DATASET_MODULE = "app.services.stock_research_v2.dataset_process"
COLUMNAR_MODULE = "app.services.stock_research_v2.columnar_process"

# One subprocess program serves every dispatched kind; the kind-to-kernel table
# inside it stays closed, so a claim's own persisted kind still selects its kernel.
TASK_MODULES = {kind: RESEARCH_MODULE for kind in ("research_ic", "backtest", "benchmark", "ml_train", "report")}
# Which of the three 021 result shapes a kind may publish.  Exactly one of
# ``experiment`` / ``report`` / ``dataset`` can ever be written for a job.
TASK_SHAPES = {"research_ic": "report", "backtest": "report", "benchmark": "report", "report": "report", "ml_train": "experiment"}
# Failure codes a dispatched kernel is allowed to persist.  Anything else, including
# free-form kernel or model text, is refused by fail_claim.
TASK_FAILURE_CODES = ("task_kind_unregistered", "task_inputs_unavailable", "task_kernel_failed", "execution_timeout")
OBSERVATION_FAILURE_CODES = ("worker_failed", "dataset_invalid", "execution_timeout")


class TaskOutcome(Contract):
    """The only message a dispatched kernel may report back to its parent.

    A subprocess never touches the database and never names a job, an experiment or
    an artifact: it reports the content digest it stored and its size, and the
    parent re-derives every identity from the fenced claim.
    """
    kind: Literal["research_ic", "backtest", "benchmark", "ml_train", "report"]
    shape: Literal["report"]
    report_sha256: str = Field(pattern=SHA256)
    report_byte_size: Annotated[int, Field(strict=True, gt=0, le=64 * 1024 * 1024)]


def task_module(kind):
    """The subprocess module that executes ``kind``; refuses anything unregistered."""
    if not isinstance(kind, str) or kind not in TASK_MODULES:
        raise JobError("task_kind_unregistered", f"未登记的研究任务类型，未派发执行：{kind!r}", 422)
    return TASK_MODULES[kind]


def task_shape(kind):
    """The single result shape 021 permits for ``kind``."""
    if not isinstance(kind, str) or kind not in TASK_SHAPES:
        raise JobError("task_kind_unregistered", f"未登记的研究任务类型，未声明结果形态：{kind!r}", 422)
    return TASK_SHAPES[kind]


async def chain_ready(session):
    """Fail-closed readiness for the 021 task chain.

    The version table, its checksum row and the three added columns must all be
    present and matching.  A missing table, a missing row, a checksum mismatch or a
    legacy column set refuses service; nothing is created or re-collected here and
    no caller can supply these identifiers.

    Every probe runs on the caller's own session and connection, never on a
    separately checked-out one: reflecting the engine from inside a transaction
    would both roll that transaction back on some pools and read a schema snapshot
    that cannot see uncommitted DDL.  ``jobs_ready``/``datasets_ready`` already
    prove their columns with a zero-row select, so this does the same.
    """
    try:
        if session.get_bind().dialect.name == "postgresql":
            if await session.scalar(text("SELECT to_regclass(quote_ident(current_schema()) || '.' || '" + CHAIN_TABLE + "')")) is None:
                return False
        elif not await session.scalar(text("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='" + CHAIN_TABLE + "'")):
            return False
        checksum = await session.scalar(text("SELECT checksum FROM " + CHAIN_TABLE + " WHERE version='" + CHAIN_VERSION + "'"))
        if checksum != hashlib.sha256(CHAIN_PATH.read_bytes()).hexdigest():
            return False
        # A zero-row select over exactly the three columns 021 adds: a legacy job
        # table fails to compile or to resolve them, which is a refusal, not a crash.
        await session.execute(select(*(getattr(StockResearchV2Job, name) for name in CHAIN_COLUMNS)).limit(0))
        return True
    except (JobError, OSError, ValueError, SQLAlchemyError):
        return False


async def require_chain(session):
    if not await chain_ready(session):
        raise JobError("task_chain_not_ready", "研究任务链021未迁移或结构校验失败；未领取研究任务", 503)


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch(SHA256, value):
        raise JobError("invalid_result_reference", "结果报告指纹无效", 422)
    return value


def _identifier(value):
    if not isinstance(value, UUID):
        raise JobError("invalid_result_reference", "结果引用ID无效", 422)
    return value


async def bound_experiment(session, claim, experiment_id, report_sha256):
    """The 019 row a research claim is about to publish, or an explicit refusal.

    Ownership, dataset binding and validation grade are all checked here: a claim can
    never publish another user's experiment, one built on a different fixed dataset,
    or one whose registered grade exceeds what the claim's own dataset qualification
    allows.  Nothing is registered, re-certified or evaluated by this call.
    """
    experiment_id, report_sha256 = _identifier(experiment_id), _digest(report_sha256)
    experiment = await session.scalar(select(StockMLExperiment).where(StockMLExperiment.id == experiment_id))
    if experiment is None or experiment.owner_id != claim.scope.owner_id or experiment.trading_mode != claim.scope.trading_mode:
        raise JobError("ml_model_not_found", "实验结果不存在或不属于当前用户/盘", 404)
    reference = DatasetRef.model_validate(claim.parameters.model_dump(mode="json")["dataset"])
    if experiment.dataset_id != reference.dataset_id or experiment.data_version != reference.data_version:
        raise JobError("job_result_mismatch", "实验结果未对应任务固定数据集", 409)
    if experiment.report_sha256 != report_sha256 or experiment.state != "ready":
        raise JobError("job_result_mismatch", "实验结果报告指纹不一致或未就绪", 409)
    # The model may not certify itself: an observation-only dataset can only publish
    # a qa_only experiment, and nothing publishes a grade its evidence cannot reach.
    expected = "qa_only" if reference.qualification == "observation_only" else "research_only"
    if experiment.validation_status != expected or not allows(reference.qualification, "ml"):
        raise JobError("job_result_mismatch", "实验结果资格高于任务数据集证据等级", 409)
    return experiment


async def bound_artifact(session, claim, artifact_id, report_sha256):
    """The 015 artifact row a research claim is about to publish, or a refusal."""
    artifact_id, report_sha256 = _identifier(artifact_id), _digest(report_sha256)
    archive = ArchiveService(session, claim.scope)
    artifact = await archive.owned_artifact(artifact_id)
    if artifact.state != "ready":
        raise JobError("job_result_mismatch", "结果产物未就绪或已隔离", 409)
    if artifact.sha256 != report_sha256:
        raise JobError("job_result_mismatch", "结果产物内容指纹与报告指纹不一致", 409)
    return artifact
