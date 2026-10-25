import asyncio
import json
import os
import sys
import threading
from datetime import datetime, timezone


def emit(message: dict) -> None:
    encoded = json.dumps(message, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    if len(encoded) > 8 * 1024 * 1024 + 4096:
        raise ValueError("process message too large")
    sys.stdout.buffer.write(encoded + b"\n")
    sys.stdout.buffer.flush()


async def calculate(kind: str, parameters: dict) -> dict:
    from sqlalchemy.engine import URL
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import load_settings
    from app.schemas.stock_research import _parse_date
    from app.schemas.stock_research_jobs import StrictAnalysisParameters, StrictBacktestParameters
    from app.services.stock_factors.engine import BacktestParams, PreprocessConfig, analyze_factor, run_portfolio_backtest
    from app.services.stock_factors.snapshots import load_versioned_market_data

    if kind not in ("analysis", "backtest"):
        raise ValueError("invalid job kind")
    body = (StrictAnalysisParameters if kind == "analysis" else StrictBacktestParameters).model_validate(parameters)
    settings = load_settings()
    database_url = URL.create("postgresql+asyncpg", username=settings.postgres_user, password=settings.postgres_password, host=settings.postgres_host, port=settings.postgres_port, database=settings.postgres_db)
    engine = create_async_engine(database_url, poolclass=NullPool, echo=False, hide_parameters=True, connect_args={"timeout": 10, "command_timeout": 30})
    try:
        data, manifest = await load_versioned_market_data(async_sessionmaker(engine, expire_on_commit=False), _parse_date(body.start_date), _parse_date(body.end_date),
            factor_ids=[body.factor_id] if kind == "analysis" else body.factor_ids, mktcap_neutral=body.preprocess.mktcap_neutral, weight=body.weight if kind == "backtest" else "equal")
        emit({"type": "snapshot", "manifest": manifest})
        if kind == "analysis":
            result = analyze_factor(data, body.factor_id, PreprocessConfig(**body.preprocess.model_dump()), body.horizon, 60, body.groups)
        else:
            result = run_portfolio_backtest(data, BacktestParams(**{**body.model_dump(), "preprocess": PreprocessConfig(**body.preprocess.model_dump())}))
        return result
    finally:
        await engine.dispose()


def main() -> int:
    watchdog = threading.Timer(125, lambda: os._exit(124))
    watchdog.daemon = True
    watchdog.start()
    try:
        raw = sys.stdin.buffer.readline(32769)
        if len(raw) > 32768:
            raise ValueError("job payload too large")
        payload = json.loads(raw)
        if not isinstance(payload, dict) or set(payload) != {"kind", "parameters", "deadline_at"}:
            raise ValueError("invalid job payload")
        deadline = datetime.fromisoformat(payload["deadline_at"])
        if deadline.tzinfo is None:
            raise ValueError("deadline requires timezone")
        remaining = (deadline - datetime.now(timezone.utc)).total_seconds() + 5
        if remaining <= 0:
            return 124
        watchdog.cancel()
        watchdog = threading.Timer(min(125, remaining), lambda: os._exit(124))
        watchdog.daemon = True
        watchdog.start()
        result = asyncio.run(calculate(payload["kind"], payload["parameters"]))
        if result.get("error"):
            emit({"type": "error", "code": result.get("error_code", "research_failed"), "message": result["error"], "status": int(result.get("status_code", 422))})
        else:
            emit({"type": "result", "result": result})
        return 0
    except Exception as error:
        from app.services.stock_factors.engine import ResearchDataError

        if isinstance(error, ResearchDataError):
            emit({"type": "error", "code": error.code, "message": str(error), "status": error.status})
        else:
            emit({"type": "error", "code": "worker_calculation_failed", "message": "研究执行失败，请凭任务编号联系管理员核对", "status": 500, "error_type": type(error).__name__})
        return 1
    finally:
        watchdog.cancel()


if __name__ == "__main__":
    raise SystemExit(main())
