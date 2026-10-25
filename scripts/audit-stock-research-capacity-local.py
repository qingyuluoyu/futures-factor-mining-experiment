import asyncio
import ctypes
from ctypes import wintypes
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sys
from time import perf_counter
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import async_sessionmaker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.stock_factors.coverage import get_research_coverage
from app.services.stock_factors.engine import BacktestParams, run_portfolio_backtest
from app.services.stock_factors.snapshots import load_versioned_market_data
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings


def process_memory():
    if os.name != "nt":
        return {"measurement": "not_available_on_this_platform"}
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [(name, ctypes.c_size_t) for name in
            ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    function = ctypes.windll.psapi.GetProcessMemoryInfo
    function.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    if not function(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return {"measurement": "windows_memory_query_failed"}
    return {"measurement": "windows_process_working_set", "working_set_bytes": counters.WorkingSetSize, "peak_working_set_bytes": counters.PeakWorkingSetSize}


async def main():
    settings = load_runtime_settings()
    if settings.postgres_host not in ("127.0.0.1", "localhost") or settings.postgres_port != 5433:
        raise RuntimeError("Only dedicated local PG5433 permitted")
    engine = create_replay_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    start, end = date(2021, 10, 16), date(2026, 10, 16)
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    report = {"audit_scope": "read_only_local_capacity_not_source_PIT_or_strategy_certification", "source_calls": 0, "model_calls": 0,
        "public_business_writes": 0, "read_at": datetime.now(timezone.utc).isoformat(), "memory_before": process_memory()}
    output = ROOT / "logs" / f"stock-research-capacity-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.json"
    try:
        async with asyncio.timeout(60):
            legacy = await get_research_coverage(factory, start, end, today=today)
            selective = await get_research_coverage(factory, start, end, today=today, factor_ids=["ret_20d"])
            report["preflight"] = {"legacy_status": legacy["status"], "legacy_panel_cells": legacy["selected"]["panel_cells"],
                "selective_status": selective["status"], "selective": selective["selected"], "dependencies": selective["dependencies"]}
            began = perf_counter()
            market, manifest = await load_versioned_market_data(factory, start, end, factor_ids=["ret_20d"])
            report["snapshot"] = {"elapsed_seconds": round(perf_counter() - began, 3), "row_counts": manifest["row_counts"],
                "coverage": manifest["coverage"], "transaction": manifest["transaction"], "data_version": manifest["data_version"],
                "snapshot_version": manifest["snapshot_version"], "point_in_time_certified": manifest["point_in_time_certified"]}
            report["memory_after_read"] = process_memory()
            began = perf_counter()
            result = run_portfolio_backtest(market, BacktestParams(factor_ids=["ret_20d"], start_date=start.isoformat(), end_date=end.isoformat(), top_n=20))
            report["backtest"] = {"elapsed_seconds": round(perf_counter() - began, 3), "status": "failed" if result.get("error") else "research_only",
                "error_code": result.get("error_code"), "error": result.get("error"), "method_version": result.get("method_version"),
                "rebalance_count": result.get("rebalance_count"), "nav_points": len(result.get("nav", []))}
            report["memory_after_calculation"] = process_memory()
            report["status"] = "observed"
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__)
        raise
    finally:
        await engine.dispose()
        with output.open("x", encoding="utf-8") as destination:
            json.dump(report, destination, ensure_ascii=False, allow_nan=False, indent=2)
        print(json.dumps(report, ensure_ascii=True, allow_nan=False))
        print("report", output.name)


if __name__ == "__main__":
    asyncio.run(main())
