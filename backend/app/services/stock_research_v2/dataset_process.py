"""Internal observation IPC entry point; never migrates or starts a worker loop."""
import asyncio
from contextlib import redirect_stdout
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import threading

from app.schemas.stock_research_v2_jobs import DatasetClaim
from app.services.stock_research_jobs import JobError
from app.services.stock_research_v2.artifacts import ArtifactStore
from app.services.stock_research_v2.claim_execution import build_claim_dataset

MAX_INPUT_BYTES = 512 * 1024


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate IPC field")
        result[key] = value
    return result


async def execute_payload(raw: bytes, root: Path, *, session_factory=None, columnar_root=None):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_INPUT_BYTES:
        raise ValueError("invalid IPC size")
    claim = DatasetClaim.model_validate(json.loads(raw, object_pairs_hook=unique_object))
    remaining = (claim.deadline_at - datetime.now(timezone.utc)).total_seconds()
    if not 0 < remaining <= 300:
        raise JobError("execution_timeout", "观察任务期限无效或已过期", 504)
    root = Path(root).absolute()
    if not root.is_dir() or root.resolve() != root:
        raise JobError("artifact_store_unavailable", "观察存储根未就绪", 503)
    store = ArtifactStore(root, max_bytes=64 * 1024 * 1024)
    options = {}
    if columnar_root is not None:
        from app.services.stock_research_v2.columnar_store import ParquetStore
        options["columnar_store"] = ParquetStore(columnar_root)
    if session_factory is not None:
        return await build_claim_dataset(session_factory, store, claim, **options)

    from sqlalchemy.engine import URL
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from app.core.config import load_settings
    settings = load_settings()
    if settings.postgres_host not in {"127.0.0.1", "localhost", "::1"} or settings.postgres_port != 5433:
        raise JobError("dataset_invalid", "观察执行器仅支持已配置原本地数据库", 422)
    url = URL.create("postgresql+asyncpg", username=settings.postgres_user, password=settings.postgres_password,
        host=settings.postgres_host, port=settings.postgres_port, database=settings.postgres_db)
    engine = create_async_engine(url, poolclass=NullPool, echo=False, hide_parameters=True,
        connect_args={"timeout": 5, "command_timeout": 30})
    try:
        async with asyncio.timeout(remaining):
            return await build_claim_dataset(async_sessionmaker(engine, expire_on_commit=False), store, claim, **options)
    finally:
        await engine.dispose()


def emit(payload):
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8")
    if len(data) > 4096:
        raise ValueError("IPC response exceeded limit")
    sys.stdout.buffer.write(data + b"\n")
    sys.stdout.buffer.flush()


def main(*, columnar=False):
    watchdog = threading.Timer(305, lambda: os._exit(124))
    watchdog.daemon = True
    watchdog.start()
    try:
        raw = sys.stdin.buffer.readline(MAX_INPUT_BYTES + 1)
        configured = os.environ.get("WENYUAN_STOCK_DATASET_ROOT")
        if not configured:
            raise ValueError("observation root not configured")
        configured_columnar = os.environ.get("WENYUAN_STOCK_COLUMNAR_ROOT") if columnar else None
        if columnar and not configured_columnar:
            raise ValueError("columnar root not configured")
        # Any incidental library output cannot become a trusted protocol message.
        with redirect_stdout(sys.stderr):
            result = asyncio.run(execute_payload(raw, Path(configured), columnar_root=Path(configured_columnar) if columnar else None))
        if result is None:
            emit({"type": "error", "code": "worker_failed", "status": 409, "message": "观察任务租约失效，未发布结果"})
            return 1
        emit({"type": "result", "result": result.model_dump(mode="json")})
        return 0
    except (ValueError, RecursionError):
        emit({"type": "error", "code": "dataset_invalid", "status": 422, "message": "观察输入或产物校验失败"})
        return 1
    except Exception:
        emit({"type": "error", "code": "worker_failed", "status": 500, "message": "观察执行失败，未发布结果"})
        return 1
    finally:
        watchdog.cancel()


if __name__ == "__main__":
    raise SystemExit(main())
