from datetime import datetime, timedelta, timezone
import json
import logging
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import defer
from pydantic import TypeAdapter, ValidationError

from app.models.gp_job import GpJob
from app.models.user import User
from app.schemas.gp_jobs import GpChampion, GpDiagnostics, LEASE_SECONDS, MAX_ACTIVE_JOBS, MAX_GENERATION_SECONDS, MAX_JOB_SECONDS, MAX_QUEUE_SECONDS, MAX_RESULT_BYTES, MAX_USER_ACTIVE_JOBS, STOP_GRACE_SECONDS, WORKFLOW_VERSION
from app.services.factor_lab.checkpoint import VERSION, canonical_bytes, digest
from app.services.factor_lab.express import to_text
from app.services.factor_mining.checkpoint_store import CheckpointError, CheckpointStore


logger = logging.getLogger("qihuo.gp_jobs")
TERMINAL = {"completed", "failed", "cancelled", "expired"}


class GpJobError(ValueError):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code, self.status = code, status


def now():
    return datetime.now(timezone.utc)


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def guard(session):
    if session.get_bind().dialect.name == "postgresql":
        await session.execute(text("SET LOCAL lock_timeout = '3s'"))
        await session.execute(text("SET LOCAL statement_timeout = '10s'"))
        await session.execute(text("SELECT pg_advisory_xact_lock(20261020009)"))


def view(row):
    return {"id": str(row.id), "input_id": str(row.input_id), "title": row.title, "status": row.status, "step": row.step,
        "generation": row.generation, "total_generations": row.total_generations, "attempts": row.attempts, "runtime_seconds": row.runtime_seconds,
        "request_id": row.request_id, "method_version": row.method_version, "workflow_version": row.workflow_version,
        "error_code": row.error_code, "has_result": row.status == "completed", "compute_stop_pending": row.lease_token is not None and row.status != "running",
        "created_at": aware(row.created_at).isoformat(), "updated_at": aware(row.updated_at).isoformat(),
        "finished_at": aware(row.finished_at).isoformat() if row.finished_at else None,
        "scope": {"market": row.market_type, "mode": row.trading_mode}, "validation_status": "research_only", "source_certified": False}


def clear_lease(row, *, conservative=False):
    if row.claimed_at is not None:
        elapsed = ((aware(row.generation_deadline) if conservative else min(now(), aware(row.generation_deadline))) - aware(row.claimed_at)).total_seconds()
        row.runtime_seconds = min(MAX_JOB_SECONDS, row.runtime_seconds + max(0, elapsed))
    row.lease_token = row.lease_until = row.generation_deadline = row.claimed_at = None
    row.updated_at = now()


async def recover_leases(session):
    await guard(session)
    rows = (await session.execute(select(GpJob).options(defer(GpJob.result_json)).where(or_(GpJob.lease_token.is_not(None), GpJob.status == "pending")).order_by(GpJob.updated_at).limit(100).with_for_update())).scalars().all()
    moment = now()
    for row in rows:
        if row.lease_token is not None:
            if row.status == "running" and (aware(row.lease_until) <= moment or aware(row.generation_deadline) <= moment):
                row.status, row.step, row.error_code, row.updated_at = "paused", "lease_lost", "worker_lease_expired", moment
            if moment >= aware(row.generation_deadline) + timedelta(seconds=STOP_GRACE_SECONDS):
                clear_lease(row, conservative=True)
        elif row.status == "pending" and aware(row.queued_at) < moment - timedelta(seconds=MAX_QUEUE_SECONDS):
            row.status, row.step, row.error_code, row.finished_at, row.updated_at = "expired", "queue_expired", "queue_expired", moment, moment
    await session.flush()


class GpJobService:
    def __init__(self, session, user_id, mode):
        if not isinstance(user_id, UUID) or mode not in ("live", "virtual"):
            raise GpJobError("invalid_scope", 403)
        self.session, self.user_id, self.mode = session, user_id, mode

    def scoped(self):
        return select(GpJob).where(GpJob.user_id == self.user_id, GpJob.market_type == "futures", GpJob.trading_mode == self.mode)

    async def row(self, identifier, *, lock=False, result=False):
        query = self.scoped().where(GpJob.id == identifier).execution_options(populate_existing=True)
        if not result:
            query = query.options(defer(GpJob.result_json))
        row = await self.session.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise GpJobError("not_found", 404)
        return row

    async def get(self, identifier):
        return view(await self.row(identifier))

    async def create(self, body, *, accepting_new):
        await guard(self.session)
        request_hash = digest({"input_id": str(body.input_id), "title": body.title, "method": VERSION, "workflow": WORKFLOW_VERSION})
        existing = await self.session.scalar(self.scoped().options(defer(GpJob.result_json)).where(GpJob.idempotency_key == body.idempotency_key).with_for_update())
        if existing is not None:
            expected = digest({"input_id": str(body.input_id), "title": body.title, "method": existing.method_version, "workflow": existing.workflow_version})
            if expected != existing.request_hash:
                raise GpJobError("idempotency_conflict")
            return view(existing)
        if not accepting_new:
            raise GpJobError("worker_unavailable", 503)
        try:
            frozen = await CheckpointStore(self.session, self.user_id, "futures", self.mode).load(body.input_id)
        except CheckpointError as error:
            raise GpJobError(error.code, error.status) from None
        if frozen.generation != 0 or await self.session.scalar(select(GpJob.id).where(GpJob.input_id == frozen.id)):
            raise GpJobError("input_already_assigned")
        await self.capacity()
        moment = now()
        row = GpJob(id=uuid4(), user_id=self.user_id, market_type="futures", trading_mode=self.mode, input_id=frozen.id,
            idempotency_key=body.idempotency_key, request_hash=request_hash, request_id=uuid4().hex, title=body.title or f"GP {frozen.input_json['symbol']}",
            status="pending", step="queued", generation=0, total_generations=frozen.config_json["generations"], attempts=0, runtime_seconds=0,
            lease_token=None, lease_until=None, generation_deadline=None, claimed_at=None, method_version=VERSION, workflow_version=WORKFLOW_VERSION,
            result_json=None, result_hash=None, error_code=None, created_at=moment, updated_at=moment, queued_at=moment, started_at=None, finished_at=None)
        self.session.add(row)
        await self.session.flush()
        logger.info("request_id=%s job_id=%s user_id=%s mode=%s operation=gp_job_created source_calls=0 model_calls=0", row.request_id, row.id, row.user_id, row.trading_mode)
        return view(row)

    async def capacity(self):
        active = select(func.count()).select_from(GpJob).where(GpJob.status.in_(("pending", "running")))
        if await self.session.scalar(active.where(GpJob.user_id == self.user_id, GpJob.trading_mode == self.mode)) >= MAX_USER_ACTIVE_JOBS:
            raise GpJobError("user_queue_full", 429)
        if await self.session.scalar(active) >= MAX_ACTIVE_JOBS:
            raise GpJobError("queue_full", 429)

    async def list(self, *, limit=20, offset=0):
        if type(limit) is not int or type(offset) is not int or not 1 <= limit <= 50 or not 0 <= offset <= 10000:
            raise GpJobError("invalid_pagination", 422)
        total = await self.session.scalar(select(func.count()).select_from(self.scoped().subquery()))
        rows = (await self.session.execute(self.scoped().options(defer(GpJob.result_json)).order_by(GpJob.created_at.desc(), GpJob.id.desc()).limit(limit).offset(offset))).scalars().all()
        return {"items": [view(row) for row in rows], "total": total, "has_more": offset + len(rows) < total}

    async def pause(self, identifier):
        await guard(self.session)
        row = await self.row(identifier, lock=True)
        if row.status == "paused":
            return view(row)
        if row.status not in ("pending", "running"):
            raise GpJobError("invalid_transition")
        row.status, row.step, row.updated_at = "paused", "user_paused", now()
        await self.session.flush()
        return view(row)

    async def cancel(self, identifier):
        await guard(self.session)
        row = await self.row(identifier, lock=True)
        if row.status == "cancelled":
            return view(row)
        if row.status in TERMINAL:
            raise GpJobError("invalid_transition")
        row.status, row.step, row.updated_at, row.finished_at = "cancelled", "user_cancelled", now(), now()
        await self.session.flush()
        return view(row)

    async def resume(self, identifier, *, accepting_new):
        await guard(self.session)
        row = await self.row(identifier, lock=True)
        if row.status != "paused":
            raise GpJobError("invalid_transition")
        if row.lease_token is not None:
            raise GpJobError("compute_stop_pending")
        if not accepting_new:
            raise GpJobError("worker_unavailable", 503)
        if row.runtime_seconds >= MAX_JOB_SECONDS:
            raise GpJobError("runtime_budget_exhausted", 422)
        await self.capacity()
        row.status, row.step, row.error_code, row.queued_at, row.updated_at = "pending", "queued", None, now(), now()
        await self.session.flush()
        return view(row)

    async def result(self, identifier):
        row = await self.row(identifier, result=True)
        if row.status != "completed" or row.result_json is None:
            raise GpJobError("result_unavailable")
        if digest(row.result_json) != row.result_hash:
            raise GpJobError("stored_result_corrupt", 503)
        return {**row.result_json, "job_id": str(row.id), "request_id": row.request_id}


async def claim_generation(session):
    await guard(session)
    await recover_leases(session)
    if await session.scalar(select(func.count()).select_from(GpJob).where(GpJob.lease_token.is_not(None))):
        return None
    row = await session.scalar(select(GpJob).options(defer(GpJob.result_json)).where(GpJob.status == "pending").order_by(GpJob.queued_at, GpJob.id).limit(1).with_for_update(skip_locked=True))
    if row is None:
        return None
    if await session.scalar(select(User.status).where(User.id == row.user_id)) != "active":
        row.status, row.step, row.error_code, row.finished_at = "failed", "user_not_active", "user_not_active", now()
        await session.flush()
        return None
    if row.method_version != VERSION or row.workflow_version != WORKFLOW_VERSION or row.runtime_seconds >= MAX_JOB_SECONDS:
        row.status, row.step, row.error_code, row.finished_at = "failed", "version_or_budget", "version_or_budget", now()
        await session.flush()
        return None
    moment = now()
    row.status, row.step, row.lease_token = "running", "computing_generation", uuid4()
    row.claimed_at = row.updated_at = moment
    row.started_at = row.started_at or moment
    row.generation_deadline = moment + timedelta(seconds=min(MAX_GENERATION_SECONDS, MAX_JOB_SECONDS - row.runtime_seconds))
    row.lease_until = min(row.generation_deadline, moment + timedelta(seconds=LEASE_SECONDS))
    row.attempts += 1
    await session.flush()
    return row


async def heartbeat_generation(session, identifier, token):
    await guard(session)
    row = await session.scalar(select(GpJob).options(defer(GpJob.result_json)).where(GpJob.id == identifier).with_for_update().execution_options(populate_existing=True))
    if row is None or row.status != "running" or row.lease_token != token or aware(row.generation_deadline) <= now() or aware(row.lease_until) <= now():
        return False
    if await session.scalar(select(User.status).where(User.id == row.user_id)) != "active":
        row.status, row.step, row.error_code, row.updated_at = "paused", "user_not_active", "user_not_active", now()
        await session.flush()
        return False
    row.lease_until, row.updated_at = min(aware(row.generation_deadline), now() + timedelta(seconds=LEASE_SECONDS)), now()
    await session.flush()
    return True


async def finish_generation(session, identifier, token, checkpoint, champions):
    await guard(session)
    row = await session.scalar(select(GpJob).where(GpJob.id == identifier).with_for_update().execution_options(populate_existing=True))
    if row is None or row.status != "running" or row.lease_token != token or aware(row.generation_deadline) <= now() or aware(row.lease_until) <= now():
        return False
    if await session.scalar(select(User.status).where(User.id == row.user_id)) != "active":
        row.status, row.step, row.error_code, row.updated_at = "paused", "user_not_active", "user_not_active", now()
        await session.flush()
        return False
    try:
        encoded = canonical_bytes(champions)
        parsed = TypeAdapter(list[GpChampion]).validate_python(champions)
        if not parsed:
            raise ValueError()
        history = {tuple(tokens): (score, metrics) for score, tokens, metrics in checkpoint["best_seen"]}
        seen = set()
        for item in parsed:
            key = tuple(item.tokens)
            score, base_metrics = history[key]
            if key in seen or score != item.composite or item.text != to_text(item.tokens):
                raise ValueError()
            seen.add(key)
            if any(name not in item.metrics or canonical_bytes(item.metrics[name]) != canonical_bytes(value) for name, value in base_metrics.items()):
                raise ValueError()
            diagnostics = GpDiagnostics.model_validate({name: value for name, value in item.metrics.items() if name not in base_metrics})
            if diagnostics.walk_forward is not None:
                wf = diagnostics.walk_forward
                if wf.n_folds != len(wf.folds) or wf.n_oos_folds != sum(not fold.in_train for fold in wf.folds) or wf.wf_stable != all(fold.test["sortino"] > 0 for fold in wf.folds if not fold.in_train):
                    raise ValueError()
    except (ValueError, TypeError, KeyError, ValidationError):
        raise GpJobError("invalid_process_result", 422) from None
    if len(parsed) > 100 or len(encoded) > MAX_RESULT_BYTES:
        raise GpJobError("result_budget_exceeded", 422)
    store = CheckpointStore(session, row.user_id, "futures", row.trading_mode)
    frozen = await store.load(row.input_id)
    cfg = frozen.config_json
    if len(parsed) > cfg["top_n"] or any(item.metrics["trials"] != cfg["population"] * cfg["generations"] + len(cfg["seed_tokens"] or []) or (item.metrics.get("walk_forward") is not None and item.metrics["walk_forward"]["n_folds"] != cfg["walk_forward_folds"]) for item in parsed):
        raise GpJobError("invalid_process_result", 422)
    advanced = await store.commit_generation(row.input_id, row.generation, checkpoint)
    row.generation, row.updated_at = advanced["generation"], now()
    if row.generation == row.total_generations:
        frozen = await store.load(row.input_id)
        row.result_json = json.loads(canonical_bytes({"champions": champions, "generation": row.generation, "input_hash": frozen.input_hash, "config_hash": frozen.config_hash,
            "checkpoint_hash": frozen.checkpoint_hash, "method_version": row.method_version, "workflow_version": row.workflow_version, "validation_status": "research_only", "source_certified": False}))
        row.result_hash, row.status, row.step, row.finished_at = digest(row.result_json), "completed", "completed", now()
    else:
        row.status, row.step, row.queued_at = "pending", "queued", now()
    clear_lease(row)
    await session.flush()
    logger.info("request_id=%s job_id=%s operation=gp_generation_saved generation=%s status=%s", row.request_id, row.id, row.generation, row.status)
    return True


async def release_stopped(session, identifier, token, *, error_code=None):
    await guard(session)
    row = await session.scalar(select(GpJob).options(defer(GpJob.result_json)).where(GpJob.id == identifier).with_for_update().execution_options(populate_existing=True))
    if row is None or row.lease_token != token:
        return False
    if row.status == "running":
        deterministic = {"gp_process_invalid", "invalid_process_result", "no_qualifying_generation", "result_too_large", "worker_protocol", "stored_input_corrupt", "stored_checkpoint_corrupt", "invalid_frozen_input", "input_generation_changed", "invalid_checkpoint", "result_budget_exceeded"}
        status = "failed" if error_code in deterministic else "expired" if error_code in {"execution_timeout", "process_budget_invalid"} else "paused"
        row.status, row.step, row.error_code = status, "execution_stopped", error_code or "execution_stopped"
        if status in TERMINAL:
            row.finished_at = now()
    clear_lease(row)
    await session.flush()
    return True
