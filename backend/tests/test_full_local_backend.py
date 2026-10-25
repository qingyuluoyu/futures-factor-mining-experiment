import inspect


def test_full_backend_uses_original_lifecycle_not_preview_or_replacement_workers():
    from scripts.full_local_backend import run_backend

    source = inspect.getsource(run_backend)
    assert 'app.main:app' in source
    assert 'access_log=False' in source
    for forbidden in ['create_preview_app', 'preview_lifespan', 'lifespan_context', 'ai_trading_loop =', 'dependency_overrides']:
        assert forbidden not in source


def test_pause_sql_keeps_existing_task_paused_and_requires_full_cas():
    from scripts.offline_user_pause import PAUSE_SQL

    update = PAUSE_SQL.split('RETURNING')[0]
    assert "pause_reason = 'user'" in update
    assert "status = 'paused'" in update.split('WHERE', 1)[1]
    assert "pause_reason = 'market_closed'" in update
    for parameter in [':task_id', ':user_id', ':trading_mode', ':expected_updated_at']:
        assert parameter in update
    assert 'status =' not in update.split('SET', 1)[1].split('WHERE', 1)[0]
    assert 'INSERT INTO ai_trading_run_logs' in PAUSE_SQL
    for forbidden in ['allocated_capital =', 'model_row_id =', 'paper_orders', 'paper_positions']:
        assert forbidden not in PAUSE_SQL


def test_offline_target_path_is_resolved_before_environment_changes_working_directory():
    from scripts.offline_user_pause import apply_target

    source = inspect.getsource(apply_target)
    assert source.index('path = path.resolve()') < source.index('configure_environment(Path')
