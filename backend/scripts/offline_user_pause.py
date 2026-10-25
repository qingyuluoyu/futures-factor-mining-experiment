import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
from uuid import UUID, NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import text


AUTHORIZATION = 'user-reply-keep-existing-ai-task-paused-20261021'
PAUSE_SQL = """
WITH changed AS (
    UPDATE ai_trading_tasks
    SET pause_reason = 'user', updated_at = :now,
        note = '用户确认保持手动暂停，开盘不自动恢复'
    WHERE id = :task_id AND user_id = :user_id AND trading_mode = :trading_mode
      AND status = 'paused' AND pause_reason = 'market_closed'
      AND strategy_type = 'ai' AND updated_at = :expected_updated_at
    RETURNING id, user_id, timeframe, symbol, strategy_type, side_mode
)
INSERT INTO ai_trading_run_logs
    (id, task_id, user_id, timeframe, symbol, strategy_type, side_mode,
     level, event, action, reason, detail, created_at)
SELECT :audit_id, id, user_id, timeframe, symbol, strategy_type, side_mode,
    'info', 'operator_pause', 'pause', '本人确认保持手动暂停，未恢复、调用模型或交易',
    CAST(:detail AS jsonb), :now
FROM changed
RETURNING id
"""


class PauseTarget(BaseModel):
    model_config = ConfigDict(extra='forbid')
    task_id: UUID
    user_id: UUID
    trading_mode: str
    expected_updated_at: datetime

    @field_validator('trading_mode')
    @classmethod
    def valid_mode(cls, value):
        if value not in ('live', 'virtual'):
            raise ValueError('Invalid trading mode')
        return value

    @field_validator('expected_updated_at')
    @classmethod
    def aware_time(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('Expected timestamp must contain timezone')
        return value


class PauseConflict(RuntimeError):
    pass


async def keep_paused(connection, target: PauseTarget) -> dict:
    parameters = target.model_dump()
    row = (await connection.execute(text(
        'SELECT status,pause_reason,strategy_type FROM ai_trading_tasks '
        'WHERE id=:task_id AND user_id=:user_id AND trading_mode=:trading_mode FOR UPDATE'
    ), parameters)).mappings().first()
    if row is None or row['status'] != 'paused' or row['strategy_type'] != 'ai':
        raise PauseConflict('Target identity, mode or paused state changed; nothing applied')
    audit_id = uuid5(NAMESPACE_URL, f'wenyuan/{AUTHORIZATION}/{target.task_id}')
    existing = await connection.scalar(text('SELECT id FROM ai_trading_run_logs WHERE id=:id'), {'id': audit_id})
    if row['pause_reason'] == 'user':
        return {'changed': False, 'status': 'paused', 'pause_reason': 'user', 'audit_id': str(existing) if existing else None}
    if existing is not None or row['pause_reason'] != 'market_closed':
        raise PauseConflict('Prior authorization cannot be reused for a newly changed intent')
    now = datetime.now(timezone.utc)
    parameters.update({
        'now': now, 'audit_id': audit_id,
        'detail': json.dumps({
            'method_version': 'offline-user-pause-v1', 'authorization_reference': AUTHORIZATION,
            'before': {'status': 'paused', 'pause_reason': 'market_closed', 'updated_at': target.expected_updated_at.isoformat()},
            'after': {'status': 'paused', 'pause_reason': 'user'},
        }),
    })
    inserted = await connection.scalar(text(PAUSE_SQL), parameters)
    if inserted is None:
        raise PauseConflict('Target timestamp changed; nothing applied')
    return {'changed': True, 'status': 'paused', 'pause_reason': 'user', 'audit_id': str(inserted)}


async def apply_target(path: Path) -> dict:
    path = path.resolve()
    from local_service import configure_environment
    configure_environment(Path(__file__).resolve().parents[2])
    from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings

    target = PauseTarget.model_validate_json(path.read_text(encoding='utf-8-sig'))
    with socket.socket() as probe:
        probe.settimeout(1)
        if probe.connect_ex(('127.0.0.1', 8002)) == 0:
            raise PauseConflict('Backend is online; refusing offline maintenance')
    settings = load_runtime_settings()
    if settings.postgres_host != '127.0.0.1' or settings.postgres_port != 5433:
        raise PauseConflict('Dedicated local database required')
    engine = create_replay_engine(settings)
    try:
        async with engine.begin() as connection:
            await connection.execute(text('SET LOCAL search_path TO public'))
            await connection.execute(text("SET LOCAL statement_timeout='5s'"))
            await connection.execute(text("SET LOCAL lock_timeout='2s'"))
            result = await keep_paused(connection, target)
        return result
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description='Explicitly authorized offline maintenance; no trading, recovery or model calls')
    parser.add_argument('--target', required=True, type=Path)
    parser.add_argument('--confirm-user-request', action='store_true', required=True)
    arguments = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(apply_target(arguments.target))))
        return 0
    except Exception as error:
        print(json.dumps({'status': 'refused_or_rolled_back', 'error_type': type(error).__name__}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
