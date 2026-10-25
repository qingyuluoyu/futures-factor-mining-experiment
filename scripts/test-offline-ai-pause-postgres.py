import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings
from scripts.offline_user_pause import PauseConflict, PauseTarget, keep_paused


async def main():
    settings = load_runtime_settings()
    assert settings.postgres_host == '127.0.0.1' and settings.postgres_port == 5433
    engine = create_replay_engine(settings)
    schema = 'private_pause_' + uuid4().hex
    assert re.fullmatch(r'private_pause_[a-f0-9]{32}', schema)
    checks = []
    created = False

    async def set_scope(connection):
        await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        await connection.execute(text("SET LOCAL statement_timeout='5s'"))

    async def fixture(connection, **patch):
        values = dict(task_id=uuid4(), user_id=uuid4(), trading_mode='live', status='paused', pause_reason='market_closed', strategy_type='ai', now=datetime.now(timezone.utc))
        values.update(patch)
        await connection.execute(text(
            'INSERT INTO ai_trading_tasks (id,user_id,trading_mode,status,pause_reason,strategy_type,updated_at,timeframe,symbol,side_mode,note,allocated_capital) '
            "VALUES (:task_id,:user_id,:trading_mode,:status,:pause_reason,:strategy_type,:now,'5m','private-fixture','both','untouched',12345.67)"
        ), values)
        return PauseTarget(task_id=values['task_id'], user_id=values['user_id'], trading_mode=values['trading_mode'], expected_updated_at=values['now'])

    try:
        async with engine.begin() as connection:
            before = await connection.scalar(text('SELECT md5(coalesce(jsonb_agg(to_jsonb(t) ORDER BY id)::text,\'[]\')) FROM public.ai_trading_tasks t'))
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            created = True
            await set_scope(connection)
            await connection.execute(text('CREATE TABLE ai_trading_tasks (id uuid PRIMARY KEY,user_id uuid NOT NULL,trading_mode text,status text,pause_reason text,strategy_type text,updated_at timestamptz,timeframe text,symbol text,side_mode text,note text,allocated_capital numeric)'))
            await connection.execute(text('CREATE TABLE ai_trading_run_logs (id uuid PRIMARY KEY,task_id uuid,user_id uuid,timeframe text,symbol text,strategy_type text,side_mode text,level text,event text,action text,reason text,detail jsonb,created_at timestamptz)'))
            target = await fixture(connection)
            original = await connection.scalar(text("SELECT to_jsonb(t)-'pause_reason'-'note'-'updated_at' FROM ai_trading_tasks t WHERE id=:id"), {'id': target.task_id})
            first = await keep_paused(connection, target)
            second = await keep_paused(connection, target)
            assert first['changed'] and not second['changed']
            assert await connection.scalar(text('SELECT count(*) FROM ai_trading_run_logs')) == 1
            unchanged = await connection.scalar(text("SELECT to_jsonb(t)-'pause_reason'-'note'-'updated_at' FROM ai_trading_tasks t WHERE id=:id"), {'id': target.task_id})
            assert original == unchanged
            checks.extend(['authorized_pause_without_recovery', 'repeat_is_idempotent', 'one_atomic_audit', 'all_other_fields_unchanged'])
        for kind in ['owner', 'mode', 'timestamp', 'running', 'strategy', 'other_reason']:
            async with engine.begin() as connection:
                await set_scope(connection)
                patch = {'status': 'running'} if kind == 'running' else {'strategy_type': 'decision'} if kind == 'strategy' else {'pause_reason': 'system'} if kind == 'other_reason' else {}
                target = await fixture(connection, **patch)
                saved = await connection.scalar(text('SELECT to_jsonb(t) FROM ai_trading_tasks t WHERE id=:id'), {'id': target.task_id})
                if kind == 'owner': target = target.model_copy(update={'user_id': uuid4()})
                if kind == 'mode': target = target.model_copy(update={'trading_mode': 'virtual'})
                if kind == 'timestamp': target = target.model_copy(update={'expected_updated_at': target.expected_updated_at + timedelta(seconds=1)})
                try:
                    await keep_paused(connection, target)
                except PauseConflict:
                    pass
                else:
                    raise AssertionError(f'{kind} unexpectedly permitted')
                actual = await connection.scalar(text('SELECT to_jsonb(t) FROM ai_trading_tasks t WHERE id=:id'), {'id': target.task_id})
                assert saved == actual
                checks.append(f'{kind}_refused_without_write')
        async with engine.begin() as connection:
            await set_scope(connection)
            target = await fixture(connection)
            await connection.execute(text("ALTER TABLE ai_trading_run_logs ADD CONSTRAINT reject_new_audits CHECK (event != 'operator_pause') NOT VALID"))
        try:
            async with engine.begin() as connection:
                await set_scope(connection)
                await keep_paused(connection, target)
        except IntegrityError:
            pass
        else:
            raise AssertionError('Audit failure did not abort transaction')
        async with engine.begin() as connection:
            await set_scope(connection)
            assert await connection.scalar(text('SELECT pause_reason FROM ai_trading_tasks WHERE id=:id'), {'id': target.task_id}) == 'market_closed'
            checks.append('audit_failure_rolls_back_pause')
            after = await connection.scalar(text('SELECT md5(coalesce(jsonb_agg(to_jsonb(t) ORDER BY id)::text,\'[]\')) FROM public.ai_trading_tasks t'))
            assert before == after
            checks.append('public_tasks_unchanged')
    finally:
        try:
            if created:
                async with engine.begin() as connection:
                    await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        finally:
            await engine.dispose()
    report = {'status': 'passed', 'checks': checks, 'scope': 'private_postgres_fixture_only', 'schema_cleaned': True, 'model_calls': 0, 'source_calls': 0}
    (ROOT / 'logs/full-startup-pause-pg.json').write_text(json.dumps(report, indent=2), encoding='utf8')
    print(json.dumps(report))


if __name__ == '__main__':
    asyncio.run(main())
