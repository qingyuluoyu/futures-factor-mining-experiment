import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.stock_raw_panel import read_raw_panel, selection
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings


def parser():
    result = argparse.ArgumentParser(description="只读固定版本原价审计导出；0上游/0模型，不是已认证回测数据集，不切换用户读路径")
    result.add_argument("--version", action="append", required=True)
    result.add_argument("--instrument", action="append", required=True)
    result.add_argument("--output", required=True, type=Path)
    return result


def output_path(value: Path, *, logs_root: Path = ROOT / "logs") -> Path:
    path = Path(value).resolve()
    if path.parent != logs_root.resolve() or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,118}\.json", path.name):
        raise ValueError("产物必须为项目logs直接目录中的新JSON文件")
    if path.exists():
        raise FileExistsError("不允许覆盖既有产物")
    return path


def publish_manifest(path: Path, manifest: dict) -> None:
    payload = json.dumps(manifest, ensure_ascii=True, allow_nan=False, indent=2).encode("utf-8")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, prefix=".stock-raw-panel-", suffix=".tmp", delete=False) as destination:
            temporary = Path(destination.name)
            destination.write(payload)
            destination.flush()
            os.fsync(destination.fileno())
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


async def read_manifest(engine, versions: list[str], instruments: list[str]) -> dict:
    async with asyncio.timeout(45):
        async with async_sessionmaker(engine, expire_on_commit=False)() as session, session.begin():
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            await session.execute(text("SET LOCAL statement_timeout = '8s'"))
            await session.execute(text("SET LOCAL lock_timeout = '2s'"))
            if await session.scalar(text("SHOW transaction_read_only")) != "on":
                raise RuntimeError("只读事务未生效，拒绝导出")
            return await read_raw_panel(session, versions, instruments, now=datetime.now(timezone.utc))


async def run(args) -> dict:
    versions, instruments = selection(args.version, args.instrument)
    path = output_path(args.output)
    settings = load_runtime_settings()
    if settings.postgres_host not in ("localhost", "127.0.0.1") or settings.postgres_port != 5433:
        raise ValueError("仅允许专用本机PG5433")
    engine = create_replay_engine(settings)
    try:
        manifest = await read_manifest(engine, versions, instruments)
    finally:
        await engine.dispose()
    publish_manifest(path, manifest)
    return {"status": "exported_internal_audit_only", "output": path.name, "content_sha256": manifest["content_sha256"],
        "versions": len(manifest["versions"]), "observations": len(manifest["observations"]), "missing": len(manifest["missing"]),
        "source_calls": 0, "model_calls": 0, "eligible_for_backtest": False, "user_read_path_changed": False}


def main() -> int:
    arguments = parser().parse_args()
    try:
        print(json.dumps(asyncio.run(run(arguments)), ensure_ascii=True, allow_nan=False))
        return 0
    except Exception as error:
        print(f"Raw panel export failed ({type(error).__name__}); no database writes or source calls; credentials omitted", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
