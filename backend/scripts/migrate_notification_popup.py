"""Only migrate notification popup preference; default/--check performs read-only validation."""

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

from sqlalchemy import text

BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = BACKEND_ROOT / "migrations" / "20261021_013_notification_popup.sql"
MIGRATION_VERSION = "20261021_013_notification_popup"
sys.path.insert(0, str(BACKEND_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from migrate_stock_replay import create_replay_engine, load_runtime_settings


async def validate_schema(connection):
    column = (await connection.execute(text(
        "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
        "WHERE table_schema=current_schema() AND table_name='user_notify_settings' AND column_name='notify_popup_enabled'"
    ))).mappings().one_or_none()
    if column is None or column["data_type"] != "boolean" or column["is_nullable"] != "NO" or column["column_default"] not in ("true", "true::boolean"):
        raise RuntimeError("notification popup column missing or incompatible")


async def migrate_notification_popup(engine, *, check=True):
    checksum = hashlib.sha256(MIGRATION_PATH.read_bytes()).hexdigest()
    async with engine.begin() as connection:
        if check:
            await connection.execute(text("SET TRANSACTION READ ONLY"))
        await connection.execute(text("SET LOCAL lock_timeout='5s'"))
        await connection.execute(text("SET LOCAL statement_timeout='30s'"))
        if not check:
            await connection.execute(text("SELECT pg_advisory_xact_lock(20261021013)"))
            await connection.execute(text(
                "CREATE TABLE IF NOT EXISTS notification_preferences_schema_versions ("
                "version VARCHAR(64) PRIMARY KEY, checksum VARCHAR(64) NOT NULL, "
                "applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            ))
        exists = (await connection.execute(text(
            "SELECT to_regclass(quote_ident(current_schema()) || '.notification_preferences_schema_versions')"
        ))).scalar_one_or_none()
        if exists is None:
            raise RuntimeError("notification popup migration not applied")
        existing = (await connection.execute(text(
            "SELECT checksum FROM notification_preferences_schema_versions WHERE version=:version"
        ), {"version": MIGRATION_VERSION})).scalar_one_or_none()
        if existing is not None and existing != checksum:
            raise RuntimeError("notification popup migration checksum mismatch")
        if existing is None:
            if check:
                raise RuntimeError("notification popup migration not applied")
            await connection.execute(text(MIGRATION_PATH.read_text(encoding="utf-8")))
            await validate_schema(connection)
            await connection.execute(text(
                "INSERT INTO notification_preferences_schema_versions(version, checksum) VALUES (:version,:checksum)"
            ), {"version": MIGRATION_VERSION, "checksum": checksum})
        else:
            await validate_schema(connection)
    return {"version": MIGRATION_VERSION, "checksum": checksum, "applied": existing is None, "checked": True}


def assert_local_settings(settings):
    if settings.postgres_host not in ("127.0.0.1", "localhost", "::1") or settings.postgres_port != 5433:
        raise RuntimeError("this migration CLI only accepts the project local PostgreSQL on loopback port 5433")


async def run(check):
    settings = load_runtime_settings()
    assert_local_settings(settings)
    engine = create_replay_engine(settings)
    try:
        print(json.dumps(await migrate_notification_popup(engine, check=check)))
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(run(not args.apply))
    except Exception as error:
        reason = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print(f"notification popup migration failed: {reason}; transaction rolled back", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
