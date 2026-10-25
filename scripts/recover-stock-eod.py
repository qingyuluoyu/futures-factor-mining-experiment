import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.stock_eod_recovery import inspect_failed_eod, recover_failed_eod
from app.services.stock_eod_sync import _reject_json_constant, _unique_json_object
from scripts.migrate_stock_eod_recovery import migrate_stock_eod_recovery
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings


async def run(args):
    snapshot_path = Path(args.snapshot).resolve(strict=True)
    destination = Path(args.output).resolve()
    if not snapshot_path.is_relative_to((ROOT / "logs").resolve()) or not destination.is_relative_to((ROOT / "logs").resolve()) or not destination.parent.is_dir():
        raise ValueError("Only operator-controlled project logs artifacts permitted")
    if snapshot_path.stat().st_size > 8 * 1024 * 1024 or args.migrate and not args.apply:
        raise ValueError("Invalid snapshot budget or migration mode")
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"), object_pairs_hook=_unique_json_object, parse_constant=_reject_json_constant)
    settings = load_runtime_settings()
    if settings.postgres_host not in {"localhost", "127.0.0.1"} or settings.postgres_port != 5433:
        raise ValueError("Only dedicated local PG5433 permitted")
    report = {"version": "eod-cached-recovery-cli-v1", "status": "claimed", "apply": args.apply, "source_calls": 0, "model_calls": 0,
              "operator_scope": "local_database_operator_not_web_admin_impersonation", "independent_certification": False}
    with destination.open("x+", encoding="utf-8") as output:
        def persist():
            report["updated_at"] = datetime.now(timezone.utc).isoformat()
            output.seek(0); json.dump(report, output, ensure_ascii=False, allow_nan=False, indent=2)
            output.truncate(); output.flush(); os.fsync(output.fileno())
        persist()
        engine = create_replay_engine(settings)
        try:
            async with asyncio.timeout(30), async_sessionmaker(engine, expire_on_commit=False)() as session, session.begin():
                if not args.apply:
                    await session.execute(text("SET TRANSACTION READ ONLY"))
                    batch, rebuilt = await inspect_failed_eod(session, snapshot)
                    report.update(status="dry_run_validated_no_writes", batch_id=str(batch.id), original_status=batch.status, data_version=rebuilt["data_version"], expected=rebuilt["expected_count"], quoted=rebuilt["covered_count"], missing=len(rebuilt["missing_codes"]), unknown=len(rebuilt["unknown_codes"]))
                else:
                    report["migration"] = await migrate_stock_eod_recovery(await session.connection(), check=not args.migrate)
                    report.update(await recover_failed_eod(session, snapshot=snapshot, idempotency_key=args.idempotency_key, operator_ref=args.operator_ref, reason=args.reason))
            if args.apply:
                report["transaction_committed"] = True
            persist()
        except BaseException as error:
            report.update(status="failed", error_type=type(error).__name__)
            persist()
            raise
        finally:
            await engine.dispose()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audited cached EOD recovery, local operator only; dry-run default, 0 source/model calls, preserves original failure journal")
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--idempotency-key", required=True, type=UUID)
    parser.add_argument("--operator-ref", default="local-console")
    parser.add_argument("--reason", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--migrate", action="store_true")
    try:
        print(json.dumps(asyncio.run(run(parser.parse_args())), ensure_ascii=False))
    except Exception as error:
        print("EOD recovery failed (" + type(error).__name__ + "); no credentials or market payload printed", file=sys.stderr)
        raise SystemExit(1)
