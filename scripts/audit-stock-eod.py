import argparse
import asyncio
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo
from uuid import uuid4

from sqlalchemy import select, text


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.models.stock_eod import StockEodBatch
from app.services.stock_eod_audit import compare_sample, select_sample
from app.services.stock_eod_sync import TeajoinEodSource
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings


async def _execute(args, before_source):
    destination = Path(args.output).resolve()
    if destination.exists():
        raise FileExistsError("审计报告已存在，不覆盖，也不重复付费查询")
    if not destination.parent.is_dir():
        raise ValueError("报告父目录必须存在")
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    if args.date is not None and args.date > today:
        raise ValueError("不能核对未来交易日")
    settings = load_runtime_settings()
    if settings.postgres_host not in ("127.0.0.1", "localhost") or settings.postgres_port != 5433:
        raise ValueError("仅允许专用本地PG5433，部署环境请使用受控审计任务")
    engine = create_replay_engine(settings)
    engine.sync_engine.echo = False
    engine.sync_engine.hide_parameters = True
    try:
        async with asyncio.timeout(10), engine.connect() as connection, connection.begin():
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            await connection.execute(text("SET LOCAL statement_timeout = '5s'"))
            statement = select(StockEodBatch.snapshot_json).where(StockEodBatch.status.in_(("completed", "partially_completed")), StockEodBatch.trade_date <= today)
            if args.date is not None:
                statement = statement.where(StockEodBatch.trade_date == args.date)
            stored = await connection.scalar(statement.order_by(StockEodBatch.trade_date.desc()).limit(1))
            if stored is None:
                raise ValueError("没有可核对的成功盘后快照")
    finally:
        await engine.dispose()
    identifiers = select_sample(stored)
    if args.verify_source:
        before_source()
        async with asyncio.timeout(30):
            observed = await TeajoinEodSource(settings.tushare_token).sample_daily(identifiers, date.fromisoformat(stored["trade_date"]))
        result = compare_sample(stored, observed.rows, identifiers)
        result.update(source_calls=1, observed_response=observed.evidence)
    else:
        result = {"audit_version": "stock-eod-sample-audit-v1", "status": "offline_validated", "independent_verification": False,
            "verification_scope": "stored_content_hash_only", "source_calls": 0, "trade_date": stored["trade_date"],
            "stored_data_version": stored["data_version"], "sample_identifiers": identifiers,
            "stored_raw_response_evidence": bool(stored.get("source_evidence"))}
    result.update(provider=stored["provider"], audited_at=datetime.now(timezone.utc).isoformat(), model_calls=0,
        database_access="read_only", production_data_modified=False, raw_snapshot_rewritten=False)
    with destination.open("x", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, allow_nan=False, indent=2)
    return result


async def run(args):
    destination = Path(args.output).resolve()
    if destination.exists():
        raise FileExistsError("审计报告已存在，不覆盖，也不重复付费查询")
    if not destination.parent.is_dir():
        raise ValueError("报告父目录必须存在")
    claim_path = destination.with_name(destination.name + ".claim.json")
    state = {"claim_version": "stock-eod-audit-claim-v1", "request_id": uuid4().hex,
        "phase": "claimed", "source_attempts": 0, "verify_source": bool(args.verify_source),
        "requested_date": args.date.isoformat() if args.date is not None else None}
    with claim_path.open("x+", encoding="utf-8") as claim:
        def persist(phase):
            state.update(phase=phase, updated_at=datetime.now(timezone.utc).isoformat())
            claim.seek(0)
            json.dump(state, claim, ensure_ascii=True, allow_nan=False, indent=2)
            claim.truncate()
            claim.flush()
            os.fsync(claim.fileno())

        def before_source():
            state["source_attempts"] = 1
            persist("source_started")

        persist("claimed")
        try:
            result = await _execute(args, before_source)
        except BaseException as error:
            state["error_type"] = type(error).__name__
            persist("failed")
            raise
        persist("completed")
        return result


def main():
    parser = argparse.ArgumentParser(description="只读A股盘后快照核对；默认离线，显式开启时仅1次上游请求/最多3只股票，不覆盖历史")
    parser.add_argument("--date", type=date.fromisoformat)
    parser.add_argument("--verify-source", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        result = asyncio.run(run(args))
        print(json.dumps({key: result[key] for key in ("status", "trade_date", "sample_identifiers", "source_calls", "independent_verification")}, ensure_ascii=True))
        return 0 if result["status"] in ("matched", "offline_validated") else 2
    except Exception as error:
        print(f"Stock EOD audit failed ({type(error).__name__}); credentials and upstream content omitted", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
