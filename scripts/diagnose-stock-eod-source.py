import argparse
import asyncio
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.models.stock_eod import StockEodBatch
from app.services.stock_eod import normalize_eod
from app.services.stock_eod_sync import EodSourceError, TeajoinEodSource, catchup_dates
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings


async def run(args):
    destination = Path(args.output).resolve()
    if not destination.is_relative_to((ROOT / "logs").resolve()) or not destination.parent.is_dir():
        raise ValueError("Only existing project logs directory permitted")
    if args.date not in catchup_dates(datetime.now(ZoneInfo("Asia/Shanghai"))):
        raise ValueError("Only last seven eligible dates permitted")
    settings = load_runtime_settings()
    if settings.postgres_host not in {"localhost", "127.0.0.1"} or settings.postgres_port != 5433:
        raise ValueError("Only dedicated local PG5433 permitted")
    engine = create_replay_engine(settings)
    try:
        async with async_sessionmaker(engine)() as session, session.begin():
            await session.execute(text("SET TRANSACTION READ ONLY"))
            await session.execute(text("SET LOCAL statement_timeout = '5s'"))
            status = await session.scalar(select(StockEodBatch.status).where(StockEodBatch.trade_date == args.date))
            if status != "failed":
                raise ValueError("Only an existing failed date may be diagnosed; no batch rewritten")
    finally:
        await engine.dispose()
    report = {"version": "eod-source-diagnostic-v1", "trade_date": args.date.isoformat(), "status": "claimed",
              "source_budget": 3, "source_attempts": [], "model_calls": 0, "public_writes": 0,
              "independent_certification": False, "batch_recovered": False}
    with destination.open("x+", encoding="utf-8") as output:
        def persist():
            report["updated_at"] = datetime.now(timezone.utc).isoformat()
            output.seek(0)
            json.dump(report, output, ensure_ascii=False, allow_nan=False, indent=2)
            output.truncate()
            output.flush()
            os.fsync(output.fileno())
        class DiagnosticSource(TeajoinEodSource):
            async def _query(self, client, name, params, fields, **kwargs):
                if len(report["source_attempts"]) >= 3:
                    raise ValueError("Diagnostic budget exceeded")
                report["source_attempts"].append(name)
                report["status"] = "source_started"
                persist()
                return await super()._query(client, name, params, fields, **kwargs)
        persist()
        try:
            async with asyncio.timeout(90):
                fetched = await DiagnosticSource(settings.tushare_token).fetch(args.date)
            if fetched is None:
                report["status"] = "source_calendar_closed_no_batch_change"
            else:
                snapshot = normalize_eod(fetched.rows, fetched.catalog, args.date, acquired_at=datetime.now(timezone.utc), has_more=fetched.has_more, source_evidence=fetched.source_evidence)
                artifact = destination.with_name(destination.stem + ".snapshot.json")
                with artifact.open("x", encoding="utf-8") as snapshot_file:
                    json.dump(snapshot, snapshot_file, ensure_ascii=False, allow_nan=False)
                report.update(status="source_validated_not_certified_or_recovered", snapshot_artifact=artifact.name, data_version=snapshot["data_version"], source_rows=snapshot["source_row_count"])
        except BaseException as error:
            report.update(status="failed", error_type=type(error).__name__, error_code=error.code if isinstance(error, EodSourceError) else "diagnostic_failed")
            persist()
            raise
        persist()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="One explicitly requested failed-day diagnostic, at most 3 source calls; does not recover or overwrite batches")
    parser.add_argument("--date", type=date.fromisoformat, required=True)
    parser.add_argument("--output", required=True)
    try:
        result = asyncio.run(run(parser.parse_args()))
        print(json.dumps(result, ensure_ascii=False))
    except Exception as error:
        print("EOD diagnostic failed (" + type(error).__name__ + "); credentials and provider response omitted", file=sys.stderr)
        raise SystemExit(1)
