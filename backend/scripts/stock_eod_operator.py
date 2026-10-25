import asyncio
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import re
from uuid import uuid4

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.stock_eod import normalize_eod
from app.services.stock_eod_gaps import validate_gap_snapshot
from app.services.stock_eod_sync import TeajoinEodSource, catchup_dates, run_eod


SOURCE_SEQUENCE = ("trade_cal", "stock_basic", "daily")
ROOT = Path(__file__).resolve().parents[2]


class SyncReceipt:
    def __init__(self, path: Path, trading_date: date, operator_ref: str, *, now: datetime, logs_root: Path):
        if type(trading_date) is not date or not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None or trading_date not in catchup_dates(now):
            raise ValueError("Only one of the last seven eligible Shanghai dates permitted")
        if not isinstance(operator_ref, str) or not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,64}", operator_ref):
            raise ValueError("Invalid local operator reference")
        self.path = Path(path).resolve()
        if self.path.parent != Path(logs_root).resolve() or not self.path.parent.is_dir() or self.path.suffix != ".json" or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,128}", self.path.name):
            raise ValueError("Receipt must be a new JSON directly in project logs")
        self.trading_date = trading_date
        self.active = False
        self.report = {"receipt_version": "stock-eod-sync-once-v1", "trade_date": trading_date.isoformat(), "operator_ref": operator_ref,
            "status": "claimed", "source_budget": 3, "source_calls": 0, "source_attempts": [], "responses": [], "model_calls": 0,
            "started_at": now.astimezone(timezone.utc).isoformat(), "write_scope": "single_eod_day_and_stock_catalog_only",
            "provider_billing": "account_metering_not_observed", "source_call_semantics": "reserved_attempts_not_billing_proof", "independent_certification": False}

    def __enter__(self):
        with self.path.open("x", encoding="utf-8") as reservation:
            json.dump(self.report, reservation, ensure_ascii=False, allow_nan=False, indent=2)
            reservation.flush()
            os.fsync(reservation.fileno())
        self.active = True
        try:
            self.persist()
        except BaseException:
            self.active = False
            raise
        return self

    def persist(self):
        if not self.active:
            raise ValueError("Receipt context is not active")
        self.report["updated_at"] = datetime.now(timezone.utc).isoformat()
        payload = json.dumps(self.report, ensure_ascii=False, allow_nan=False, indent=2)
        temporary = self.path.with_name("." + self.path.name + "." + uuid4().hex + ".tmp")
        try:
            with temporary.open("x", encoding="utf-8") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def begin_attempt(self, name: str):
        attempts = self.report["source_attempts"]
        if self.report["status"] not in ("claimed", "source_started") or len(attempts) >= 3 or name != SOURCE_SEQUENCE[len(attempts)]:
            raise ValueError("Source budget or ordered whitelist exceeded")
        attempts.append(name)
        self.report["source_calls"] = len(attempts)
        self.report["status"] = "source_started"
        self.persist()

    def record_response(self, evidence: dict):
        attempts, responses = self.report["source_attempts"], self.report["responses"]
        if not isinstance(evidence, dict) or not attempts or len(responses) != len(attempts) - 1 or evidence.get("api_name") != attempts[-1]:
            raise ValueError("Source response order conflict")
        if not isinstance(evidence.get("payload_sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", evidence["payload_sha256"]):
            raise ValueError("Source response hash invalid")
        if type(evidence.get("byte_count")) is not int or not 1 <= evidence["byte_count"] <= 8 * 1024 * 1024 or type(evidence.get("row_count")) is not int or not 0 <= evidence["row_count"] <= 10000:
            raise ValueError("Source response size invalid")
        stamp = datetime.fromisoformat(evidence["received_at"])
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("Source response timestamp invalid")
        responses.append({key: evidence[key] for key in ("api_name", "payload_sha256", "byte_count", "received_at", "row_count")})
        self.persist()

    def finish(self, result: dict):
        if result.get("status") not in ("completed", "partially_completed", "no_data", "already_recorded") or result.get("trade_date") != self.trading_date.isoformat():
            raise ValueError("Unexpected single-day database result")
        self.report["database_result_observed"] = {key: result[key] for key in ("status", "trade_date", "job_id", "data_version") if key in result}
        self.report["status"] = result["status"]
        self.persist()

    def __exit__(self, error_type, error, traceback):
        try:
            if error_type is not None:
                code = getattr(error, "code", None)
                self.report.update(status="audit_failed_after_database_result" if "database_result_observed" in self.report else "cancelled" if isinstance(error, asyncio.CancelledError) else "failed",
                    error_type=error_type.__name__, error_code=code if isinstance(code, str) and re.fullmatch(r"[a-z0-9_]{1,64}", code) else "operator_execution_failed")
                self.persist()
            elif self.report["status"] in ("claimed", "source_started", "source_validated"):
                self.report["status"] = "incomplete"
                self.persist()
        finally:
            self.active = False
        return False


class AuditedEodSource(TeajoinEodSource):
    def __init__(self, token: str, receipt: SyncReceipt):
        super().__init__(token)
        self.receipt = receipt

    async def _query(self, client, name, params, fields, **kwargs):
        self.receipt.begin_attempt(name)
        result = await super()._query(client, name, params, fields, **kwargs)
        self.receipt.record_response(result.evidence)
        return result

    async def fetch(self, trading_date: date, cached_daily=None):
        if trading_date != self.receipt.trading_date or cached_daily is not None:
            raise ValueError("Only the audited live single date permitted")
        fetched = await super().fetch(trading_date)
        if fetched is not None:
            snapshot = normalize_eod(fetched.rows, fetched.catalog, trading_date, acquired_at=datetime.now(timezone.utc), has_more=fetched.has_more, source_evidence=fetched.source_evidence)
            validate_gap_snapshot(snapshot, trading_date, snapshot["data_version"])
            self.receipt.report.update(status="source_validated", validated_snapshot={"data_version": snapshot["data_version"], "source_rows": snapshot["source_row_count"], "expected": snapshot["expected_count"], "quoted": snapshot["covered_count"], "missing": len(snapshot["missing_codes"]), "unknown": len(snapshot["unknown_codes"])})
            self.receipt.persist()
        return fetched


async def run_operator_sync(session_factory, source: AuditedEodSource, receipt: SyncReceipt) -> dict:
    async with asyncio.timeout(140):
        result = await run_eod(session_factory, source, receipt.trading_date)
    receipt.finish(result)
    return receipt.report


async def execute_once(trading_date: date, output: Path, operator_ref: str) -> dict:
    from scripts.migrate_stock_eod import migrate_stock_eod
    from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings

    settings = load_runtime_settings()
    if settings.postgres_host not in ("localhost", "127.0.0.1") or settings.postgres_port != 5433:
        raise ValueError("Only dedicated local PG5433 permitted")
    with SyncReceipt(output, trading_date, operator_ref, now=datetime.now(timezone.utc), logs_root=ROOT / "logs") as receipt:
        engine = create_replay_engine(settings)
        try:
            await migrate_stock_eod(engine, check=True)
            return await run_operator_sync(async_sessionmaker(engine, expire_on_commit=False), AuditedEodSource(settings.tushare_token, receipt), receipt)
        finally:
            await engine.dispose()
