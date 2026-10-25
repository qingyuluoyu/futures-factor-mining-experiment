import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.deps import get_auth_context
from app.schemas.stock_archive_recovery import RecoveryRequest
from app.services.stock_archive_recovery import RecoveryError, inspect_recovery, run_recovery
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings


def parser():
    result = argparse.ArgumentParser(description="管理员显式恢复已存EOD影子归档；默认只读，不接行情/模型/真实资金")
    result.add_argument("--source-job-id", required=True, type=UUID)
    result.add_argument("--expected-data-version", required=True)
    result.add_argument("--idempotency-key", required=True, type=UUID)
    result.add_argument("--reason", required=True)
    result.add_argument("--apply", action="store_true")
    return result


async def run(args):
    token = os.environ.get("STOCK_ARCHIVE_RECOVERY_TOKEN", "").strip()
    if not token:
        raise RecoveryError("access_token_required", 401, "必须通过固定环境变量提供有效管理员访问令牌")
    body = RecoveryRequest.model_validate({"source_job_id": args.source_job_id, "expected_data_version": args.expected_data_version,
        "idempotency_key": args.idempotency_key, "reason": args.reason})
    settings = load_runtime_settings()
    if settings.postgres_host not in ("localhost", "127.0.0.1") or settings.postgres_port != 5433:
        raise RecoveryError("local_database_required", 400, "仅允许专用本地PG5433")
    engine = create_replay_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with asyncio.timeout(30):
            async with factory() as session, session.begin():
                await session.execute(text("SET TRANSACTION READ ONLY"))
                await session.execute(text("SET LOCAL statement_timeout = '5s'"))
                context = await get_auth_context(token=token, session=session, settings=settings)
                if context.user.role != "admin":
                    raise RecoveryError("admin_required", 403, "需要管理员权限")
                operator_id = context.user.id
                if not args.apply:
                    return await inspect_recovery(session, body, operator_id=operator_id, now=datetime.now(timezone.utc))
            from scripts.migrate_stock_eod import migrate_stock_eod
            from scripts.migrate_stock_daily_archive import migrate_stock_daily_archive
            from scripts.migrate_stock_archive_jobs import migrate_stock_archive_jobs
            from scripts.migrate_stock_archive_recovery import migrate_stock_archive_recovery
            for migration in (migrate_stock_eod, migrate_stock_daily_archive, migrate_stock_archive_jobs, migrate_stock_archive_recovery):
                await migration(engine, check=True)
            return await run_recovery(factory, body, operator_id=operator_id)
    finally:
        await engine.dispose()


def main() -> int:
    args = parser().parse_args()
    try:
        report = asyncio.run(run(args))
        print(json.dumps(report, ensure_ascii=True, allow_nan=False))
        return 0 if report["status"] in ("completed", "dry_run") else 1
    except RecoveryError as error:
        print(json.dumps({"status": "rejected", "error_code": error.code, "http_status": error.status, "source_calls": 0, "model_calls": 0}))
    except HTTPException as error:
        print(json.dumps({"status": "rejected", "error_code": "invalid_or_frozen_access_token", "http_status": error.status_code}))
    except ValidationError:
        print(json.dumps({"status": "rejected", "error_code": "invalid_request"}))
    except Exception as error:
        print(json.dumps({"status": "failed", "error_code": "recovery_execution_failed", "error_type": type(error).__name__,
            "state": "check_persisted_recovery_before_retry", "credentials_omitted": True}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
