"""FastAPI 应用入口"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.ai_chat import router as ai_chat_router
from app.api.ai_conversation import router as ai_conversation_router
from app.api.ai_knowledge import router as ai_knowledge_router
from app.api.ai_memory import router as ai_memory_router
from app.api.ai_model import router as ai_model_router
from app.api.ai_provider import router as ai_provider_router
from app.api.strategy_folders import router as strategy_folders_router
from app.api.ai_trading_favorites import router as ai_trading_favorites_router
from app.api.ai_skills import router as ai_skills_router
from app.api.ai_tools import router as ai_tools_router
from app.api.kline import router as kline_router
from app.api.market import router as market_router
from app.api.contracts import router as contracts_router
from app.api.news import router as news_router
from app.api.users import router as users_router
from app.api.watchlist import router as watchlist_router
from app.api.ws import router as ws_router
from app.api.ws import (
    start_big_order_subscriber,
    start_notify_subscriber,
    start_ops_alert_subscriber,
    stop_big_order_subscriber,
    stop_notify_subscriber,
    stop_ops_alert_subscriber,
)
from app.api.paper import router as paper_router
from app.api.ai_trading import router as ai_trading_router
from app.api.ai_anchor import router as ai_anchor_router
from app.api.backtest import router as backtest_router
from app.api.admin import router as admin_router
from app.api.notifications import router as notifications_router
from app.api.price_alerts import router as price_alerts_router
from app.api.big_orders import router as big_orders_router
from app.api.task_alerts import router as task_alerts_router
from app.api.showcase import router as showcase_router
from app.api.factor_lab import router as factor_lab_router
from app.api.factor_mining import router as factor_mining_router
from app.api.indicators import router as indicators_router
from app.api.stock import router as stock_router
from app.api.stock_market import router as stock_market_router
from app.api.stock_replay import router as stock_replay_router
from app.api.stock_factors import router as stock_factors_router
from app.api.stock_research_jobs import router as stock_research_jobs_router, research_ready
from app.api.stock_research_reports import router as stock_research_reports_router
from app.services.stock_research_worker import StockResearchWorker
from app.api.gp_jobs import router as gp_jobs_router
from app.services.factor_mining.startup import start_gp_worker
from app.services.stock_eod_sync import StockEodWorker, TeajoinEodSource
from app.services.stock_archive_worker import StockArchiveWorker
from app.services.database_readiness import verify_database_schema
from app.api.public import router as public_router
from app.api.admin_api_keys import router as admin_api_keys_router
from app.core.config import load_settings
from app.core.database import create_database_engine, create_session_factory
from app.core.logger import setup_logger
from app.core.redis import close_redis_connection, create_redis_connection
from app.models.base import Base
from app.services.scheduler import set_session_factory, start_scheduler, stop_scheduler
from scripts.migrate_stock_eod import migrate_stock_eod
from scripts.migrate_stock_daily_archive import migrate_stock_daily_archive
from scripts.migrate_stock_archive_jobs import migrate_stock_archive_jobs


async def _init_tables(engine) -> None:
    """根据 ORM 模型自动创建数据库表（开发阶段使用）"""
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError, ProgrammingError

    async with engine.begin() as conn:
        # 导入模型确保 Base.metadata 已注册
        import app.models.user  # noqa: F401
        import app.models.watchlist  # noqa: F401
        import app.models.ai_provider  # noqa: F401
        import app.models.ai_model  # noqa: F401
        import app.models.ai_conversation  # noqa: F401
        import app.models.ai_skill  # noqa: F401
        import app.models.paper  # noqa: F401
        import app.models.ai_trading  # noqa: F401
        import app.models.ai_trading.run_log  # noqa: F401
        import app.models.system_settings  # noqa: F401
        import app.models.factor_lab  # noqa: F401
        import app.models.big_order  # noqa: F401
        import app.models.task_alert  # noqa: F401
        import app.models.factor_mining  # noqa: F401
        import app.models.kline_minute  # noqa: F401
        import app.models.stock  # noqa: F401 —— A股标的/估值/财务（双市场）
        import app.models.api_key  # noqa: F401
        import app.models.strategy_folder  # noqa: F401
        import app.models.ai_trading.favorite  # noqa: F401
        import app.models.ai_anchor  # noqa: F401

        # 多 worker 并发 create_all 可能抢类型名，忽略“已存在”竞态
        try:
            await conn.run_sync(Base.metadata.create_all)
        except (IntegrityError, ProgrammingError):
            pass
        # AI 渠道：思考模式三态开关（NULL=跟随模型自动，仅 Claude 规范生效）
        await conn.execute(
            text(
                "ALTER TABLE ai_providers "
                "ADD COLUMN IF NOT EXISTS thinking_enabled BOOLEAN"
            )
        )
        # AI 渠道枚举扩值：api_type 新增 jev（TypeSafe System One 决策模型）。
        # 已有库的枚举不会随 create_all 更新，需显式 ADD VALUE；
        # PG12+ 才允许事务内执行，老版本失败时忽略（jev 渠道不可用但不影响启动）。
        try:
            await conn.execute(
                text("ALTER TYPE api_type_enum ADD VALUE IF NOT EXISTS 'jev'")
            )
        except Exception:
            pass
        # AI 交易：多周期共振 + AI 每周期 K 线根数（仅 AI 策略生效）
        await conn.execute(
            text(
                "ALTER TABLE ai_trading_tasks "
                "ADD COLUMN IF NOT EXISTS extra_timeframes JSONB "
                "NOT NULL DEFAULT '[]'::jsonb"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE ai_trading_tasks "
                "ADD COLUMN IF NOT EXISTS ai_bars_limit INTEGER "
                "NOT NULL DEFAULT 40"
            )
        )
        # 决策模型（System One）评估间隔秒：仅 decision 策略消费
        await conn.execute(
            text(
                "ALTER TABLE ai_trading_tasks "
                "ADD COLUMN IF NOT EXISTS decision_interval_sec INTEGER "
                "NOT NULL DEFAULT 60"
            )
        )
        # 量化任务快频评估间隔秒：全部量化策略消费（decision 用 decision_interval_sec）。
        # 原 factor_interval_sec（PR#305 仅 factor）扩展更名；有旧列时拷贝值后删除
        await conn.execute(
            text(
                "ALTER TABLE ai_trading_tasks "
                "ADD COLUMN IF NOT EXISTS quant_interval_sec INTEGER "
                "NOT NULL DEFAULT 0"
            )
        )
        # 波段功能下线：停掉存量 swing_pivot / swing_pivot_v2 任务
        # （策略代码已删，继续 running 只会反复报错；历史记录保留）
        await conn.execute(
            text(
                "UPDATE ai_trading_tasks SET status = 'stopped' "
                "WHERE strategy_type IN ('swing_pivot', 'swing_pivot_v2') "
                "AND status IN ('running', 'paused')"
            )
        )
        # 波段遗留列（signal_stop_*）不再使用，库中残留列不回填不删除
        await conn.execute(
            text(
                "DO $$ BEGIN"
                " IF EXISTS (SELECT 1 FROM information_schema.columns"
                "  WHERE table_name = 'ai_trading_tasks'"
                "    AND column_name = 'factor_interval_sec') THEN"
                "  UPDATE ai_trading_tasks"
                "     SET quant_interval_sec = factor_interval_sec"
                "   WHERE quant_interval_sec = 0;"
                "  ALTER TABLE ai_trading_tasks DROP COLUMN factor_interval_sec;"
                " END IF;"
                "END $$"
            )
        )
        # AI 看盘主播：方向偏好 + 关键压力/支撑位（已有表补列）
        await conn.execute(
            text(
                "ALTER TABLE ai_anchor_tasks "
                "ADD COLUMN IF NOT EXISTS direction_mode VARCHAR(8) "
                "NOT NULL DEFAULT 'any'"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE ai_anchor_broadcasts "
                "ADD COLUMN IF NOT EXISTS key_levels JSONB"
            )
        )
        # 主播持仓互动播报：用户手动录入的持仓（可空 JSONB）
        await conn.execute(
            text(
                "ALTER TABLE ai_anchor_tasks "
                "ADD COLUMN IF NOT EXISTS manual_position JSONB"
            )
        )
        # 主播操盘策略（aggressive/balanced/conservative）
        await conn.execute(
            text(
                "ALTER TABLE ai_anchor_tasks "
                "ADD COLUMN IF NOT EXISTS strategy VARCHAR(16) "
                "NOT NULL DEFAULT 'balanced'"
            )
        )
        # 主播交易风格（short/mid/long，与操盘策略正交）
        await conn.execute(
            text(
                "ALTER TABLE ai_anchor_tasks "
                "ADD COLUMN IF NOT EXISTS horizon VARCHAR(16) "
                "NOT NULL DEFAULT 'short'"
            )
        )
        # 主播多分时段：timeframes JSONB 数组（最多3个，首个为主时段）；
        # 存量任务回填为现有单周期数组；timeframe 列继续冗余主时段
        await conn.execute(
            text(
                "ALTER TABLE ai_anchor_tasks "
                "ADD COLUMN IF NOT EXISTS timeframes JSONB"
            )
        )
        await conn.execute(
            text(
                "UPDATE ai_anchor_tasks "
                "SET timeframes = to_jsonb(ARRAY[timeframe]) "
                "WHERE timeframes IS NULL"
            )
        )
        # 增量列：已有表 create_all 不会 ALTER，这里补齐
        # 用量配额：system_settings 全局默认 + users per-user 覆盖
        await conn.execute(
            text(
                "ALTER TABLE system_settings "
                "ADD COLUMN IF NOT EXISTS max_tasks_per_user INTEGER "
                "NOT NULL DEFAULT 5"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE system_settings "
                "ADD COLUMN IF NOT EXISTS factor_lab_daily_limit INTEGER "
                "NOT NULL DEFAULT 3"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE system_settings "
                "ADD COLUMN IF NOT EXISTS backtest_daily_limit INTEGER "
                "NOT NULL DEFAULT 2"
            )
        )
        # 交易时段行情/K线/WS 刷新间隔秒数（1-10，默认 1）
        await conn.execute(
            text(
                "ALTER TABLE system_settings "
                "ADD COLUMN IF NOT EXISTS market_refresh_interval_sec INTEGER "
                "NOT NULL DEFAULT 5"
            )
        )
        # per-channel 刷新间隔覆盖（JSON，如 {"sina":3}）
        await conn.execute(
            text(
                "ALTER TABLE system_settings "
                "ADD COLUMN IF NOT EXISTS market_refresh_intervals VARCHAR(256) "
                "NOT NULL DEFAULT '{}'"
            )
        )
        # ----- VVTR（长尾猴量化）数据源配置 -----
        await conn.execute(
            text(
                "ALTER TABLE system_settings "
                "ADD COLUMN IF NOT EXISTS vvtr_enabled BOOLEAN "
                "NOT NULL DEFAULT false"
            )
        )
        # ----- 双市场偏好：users.market_type（futures/stock）-----
        await conn.execute(
            text(
                "ALTER TABLE users "
                "ADD COLUMN IF NOT EXISTS market_type VARCHAR(16) "
                "NOT NULL DEFAULT 'futures'"
            )
        )
        # ----- 双市场隔离：kline_history / watchlist 增加 market_type -----
        await conn.execute(
            text(
                "ALTER TABLE kline_history "
                "ADD COLUMN IF NOT EXISTS market_type VARCHAR(16) "
                "NOT NULL DEFAULT 'futures'"
            )
        )
        # 存量回填：6位代码视为 A股，其余期货
        await conn.execute(
            text(
                "UPDATE kline_history SET market_type = 'stock' "
                "WHERE code ~ '^[0-9]{6}$' AND market_type = 'futures'"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE watchlist "
                "ADD COLUMN IF NOT EXISTS market_type VARCHAR(16) "
                "NOT NULL DEFAULT 'futures'"
            )
        )
        await conn.execute(
            text(
                "UPDATE watchlist SET market_type = 'stock' "
                "WHERE contract_symbol ~ '^[0-9]{6}$' AND market_type = 'futures'"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE system_settings "
                "ADD COLUMN IF NOT EXISTS vvtr_api_key VARCHAR(512) "
                "NOT NULL DEFAULT ''"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE system_settings "
                "ADD COLUMN IF NOT EXISTS vvtr_mobile VARCHAR(32) "
                "NOT NULL DEFAULT ''"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE system_settings "
                "ADD COLUMN IF NOT EXISTS vvtr_base_url VARCHAR(128) "
                "NOT NULL DEFAULT 'https://rest.vvtr.com/v1'"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE system_settings "
                "ADD COLUMN IF NOT EXISTS vvtr_ws_url VARCHAR(128) "
                "NOT NULL DEFAULT 'wss://rest.vvtr.com/v2/connect'"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE system_settings "
                "ADD COLUMN IF NOT EXISTS vvtr_quotes_enabled BOOLEAN "
                "NOT NULL DEFAULT true"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE system_settings "
                "ADD COLUMN IF NOT EXISTS vvtr_ws_enabled BOOLEAN "
                "NOT NULL DEFAULT true"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE system_settings "
                "ADD COLUMN IF NOT EXISTS vvtr_kline_enabled BOOLEAN "
                "NOT NULL DEFAULT false"
            )
        )
        # CTP 中转模拟行情（ctp_sim）：管理端可配前置/账号（bridge 读 Redis 镜像）
        await conn.execute(
            text(
                "ALTER TABLE system_settings "
                "ADD COLUMN IF NOT EXISTS ctp_sim_enabled BOOLEAN "
                "NOT NULL DEFAULT false"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE system_settings "
                "ADD COLUMN IF NOT EXISTS ctp_sim_md_front VARCHAR(128) "
                "NOT NULL DEFAULT ''"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE system_settings "
                "ADD COLUMN IF NOT EXISTS ctp_sim_broker_id VARCHAR(16) "
                "NOT NULL DEFAULT '9999'"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE system_settings "
                "ADD COLUMN IF NOT EXISTS ctp_sim_investor_id VARCHAR(32) "
                "NOT NULL DEFAULT 'qihuo_sim'"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE system_settings "
                "ADD COLUMN IF NOT EXISTS ctp_sim_password VARCHAR(512) "
                "NOT NULL DEFAULT ''"
            )
        )
        # 策略收藏夹：因子收藏挂到文件夹（文件夹删除回到未分类）
        await conn.execute(
            text(
                "ALTER TABLE factor_favorites "
                "ADD COLUMN IF NOT EXISTS folder_id UUID "
                "REFERENCES strategy_folders(id) ON DELETE SET NULL"
            )
        )
        # 收藏手动排序（拖拽排序持久化）
        await conn.execute(
            text(
                "ALTER TABLE factor_favorites "
                "ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE task_favorites "
                "ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0"
            )
        )
        # 因子 token 编码版本：默认 1（旧编码），迁移脚本执行后置为 2。
        # 不在此处自动改写用户数据，迁移必须由运维显式执行。
        await conn.execute(
            text(
                "ALTER TABLE system_settings "
                "ADD COLUMN IF NOT EXISTS factor_token_encoding_version INTEGER "
                "NOT NULL DEFAULT 1"
            )
        )
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN IF NOT EXISTS max_tasks_override INTEGER")
        )
        await conn.execute(
            text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                "factor_lab_daily_override INTEGER"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                "backtest_daily_override INTEGER"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE ai_trading_tasks "
                "ADD COLUMN IF NOT EXISTS pause_reason VARCHAR(32)"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE ai_trading_tasks "
                "ADD COLUMN IF NOT EXISTS strategy_type VARCHAR(32) "
                "DEFAULT 'ai'"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE ai_trading_tasks "
                "ADD COLUMN IF NOT EXISTS strategy_params JSONB "
                "DEFAULT '{}'::jsonb"
            )
        )
        # AI 资金仓 / 策略风格 / 最长持仓 / 用户提示词
        await conn.execute(
            text(
                "ALTER TABLE ai_trading_tasks "
                "ADD COLUMN IF NOT EXISTS allocated_capital "
                "NUMERIC(18,2) DEFAULT 0"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE ai_trading_tasks "
                "ADD COLUMN IF NOT EXISTS risk_style "
                "VARCHAR(16) DEFAULT 'balanced'"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE ai_trading_tasks "
                "ADD COLUMN IF NOT EXISTS max_hold_days "
                "INTEGER DEFAULT 10"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE ai_trading_tasks "
                "ADD COLUMN IF NOT EXISTS custom_prompt_enabled "
                "BOOLEAN DEFAULT false"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE ai_trading_tasks "
                "ADD COLUMN IF NOT EXISTS custom_prompt TEXT"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE ai_trading_tasks "
                "ADD COLUMN IF NOT EXISTS position_opened_at "
                "TIMESTAMPTZ"
            )
        )
        # 实际运行时长累计（仅 running 段，扣除手动/休市暂停段）
        await conn.execute(
            text(
                "ALTER TABLE ai_trading_tasks "
                "ADD COLUMN IF NOT EXISTS runtime_seconds "
                "INTEGER NOT NULL DEFAULT 0"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE ai_trading_tasks "
                "ADD COLUMN IF NOT EXISTS run_started_at TIMESTAMPTZ"
            )
        )
        # 历史 running 任务：本次运行段从重启时刻起算（历史累计无法回溯）
        await conn.execute(
            text(
                "UPDATE ai_trading_tasks SET run_started_at = NOW() "
                "WHERE status = 'running' AND run_started_at IS NULL"
            )
        )
        # 胜率统计字段：每次平仓后增量更新（按平仓笔数计）
        await conn.execute(
            text(
                "ALTER TABLE ai_trading_tasks "
                "ADD COLUMN IF NOT EXISTS win_count INTEGER DEFAULT 0"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE ai_trading_tasks "
                "ADD COLUMN IF NOT EXISTS loss_count INTEGER DEFAULT 0"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE ai_trading_tasks "
                "ADD COLUMN IF NOT EXISTS trade_count INTEGER DEFAULT 0"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE ai_trading_tasks "
                "ADD COLUMN IF NOT EXISTS win_rate NUMERIC(5,2) DEFAULT 0"
            )
        )
        # 手数范围 / 资金使用比例范围
        await conn.execute(
            text(
                "ALTER TABLE ai_trading_tasks "
                "ADD COLUMN IF NOT EXISTS qty_min INTEGER DEFAULT 1"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE ai_trading_tasks "
                "ADD COLUMN IF NOT EXISTS qty_max INTEGER DEFAULT 1"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE ai_trading_tasks "
                "ADD COLUMN IF NOT EXISTS capital_usage_min_pct "
                "NUMERIC(8,2) DEFAULT 0"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE ai_trading_tasks "
                "ADD COLUMN IF NOT EXISTS capital_usage_max_pct "
                "NUMERIC(8,2) DEFAULT 100"
            )
        )
        # 兼容旧任务：qty 范围默认对齐 fixed_qty
        await conn.execute(
            text(
                "UPDATE ai_trading_tasks SET qty_min = fixed_qty "
                "WHERE qty_min IS NULL OR qty_min < 1"
            )
        )
        await conn.execute(
            text(
                "UPDATE ai_trading_tasks SET qty_max = GREATEST(fixed_qty, qty_min) "
                "WHERE qty_max IS NULL OR qty_max < qty_min"
            )
        )
        # 量化任务可不绑模型
        await conn.execute(
            text(
                "ALTER TABLE ai_trading_tasks "
                "ALTER COLUMN model_row_id DROP NOT NULL"
            )
        )
        # 任务头像图标 slug（可空）
        await conn.execute(
            text(
                "ALTER TABLE ai_trading_tasks "
                "ADD COLUMN IF NOT EXISTS icon VARCHAR(48)"
            )
        )
        # per-task 资金仓余额（独立子账本）
        await conn.execute(
            text(
                "ALTER TABLE ai_trading_tasks "
                "ADD COLUMN IF NOT EXISTS capital_balance NUMERIC(18,2) DEFAULT 0"
            )
        )
        # 历史 AI 任务 capital_balance 初始化 = allocated_capital（兼容）
        await conn.execute(
            text(
                "UPDATE ai_trading_tasks SET capital_balance = allocated_capital "
                "WHERE capital_balance = 0 AND allocated_capital > 0 "
                "AND strategy_type = 'ai'"
            )
        )
        # 实时行情渠道（sina / openctp / real_ctp / vvtr）
        await conn.execute(
            text(
                "ALTER TABLE system_settings "
                "ADD COLUMN IF NOT EXISTS market_data_channel "
                "VARCHAR(16) DEFAULT 'sina'"
            )
        )
        # 备用行情渠道 + 主备自动切换开关
        await conn.execute(
            text(
                "ALTER TABLE system_settings "
                "ADD COLUMN IF NOT EXISTS backup_channel "
                "VARCHAR(16) DEFAULT 'sina'"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE system_settings "
                "ADD COLUMN IF NOT EXISTS channel_auto_switch "
                "BOOLEAN DEFAULT true"
            )
        )
        # K 线历史数据源：主备/轮询模式开关 + 源列表
        await conn.execute(
            text(
                "ALTER TABLE system_settings "
                "ADD COLUMN IF NOT EXISTS kline_source_mode "
                "BOOLEAN DEFAULT false"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE system_settings "
                "ADD COLUMN IF NOT EXISTS kline_source_order "
                "VARCHAR(128) DEFAULT 'sina,tqsdk,eastmoney'"
            )
        )
        # K 线历史数据源：主渠道 + 备用渠道（替代旧的 mode/order）
        await conn.execute(
            text(
                "ALTER TABLE system_settings "
                "ADD COLUMN IF NOT EXISTS kline_primary_source "
                "VARCHAR(32) DEFAULT 'eastmoney'"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE system_settings "
                "ADD COLUMN IF NOT EXISTS kline_backup_sources "
                "VARCHAR(128) DEFAULT 'sina,tqsdk'"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE system_settings "
                "ADD COLUMN IF NOT EXISTS kline_sync_enabled "
                "BOOLEAN DEFAULT true"
            )
        )
        # 实盘/虚拟盘：trading_mode 列 + 唯一约束迁移
        await conn.execute(
            text(
                "ALTER TABLE paper_accounts "
                "ADD COLUMN IF NOT EXISTS trading_mode VARCHAR(16) "
                "DEFAULT 'live'"
            )
        )
        await conn.execute(
            text(
                "UPDATE paper_accounts SET trading_mode = 'live' "
                "WHERE trading_mode IS NULL OR trading_mode = ''"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE paper_trade_settings "
                "ADD COLUMN IF NOT EXISTS trading_mode VARCHAR(16) "
                "DEFAULT 'live'"
            )
        )
        await conn.execute(
            text(
                "UPDATE paper_trade_settings SET trading_mode = 'live' "
                "WHERE trading_mode IS NULL OR trading_mode = ''"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE watchlist "
                "ADD COLUMN IF NOT EXISTS trading_mode VARCHAR(16) "
                "DEFAULT 'live'"
            )
        )
        await conn.execute(
            text(
                "UPDATE watchlist SET trading_mode = 'live' "
                "WHERE trading_mode IS NULL OR trading_mode = ''"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE ai_trading_tasks "
                "ADD COLUMN IF NOT EXISTS trading_mode VARCHAR(16) "
                "DEFAULT 'live'"
            )
        )
        await conn.execute(
            text(
                "UPDATE ai_trading_tasks SET trading_mode = 'live' "
                "WHERE trading_mode IS NULL OR trading_mode = ''"
            )
        )
        # 去掉旧 user_id 唯一（含约束与唯一索引），改 (user_id, trading_mode)
        await conn.execute(
            text(
                "ALTER TABLE paper_accounts "
                "DROP CONSTRAINT IF EXISTS paper_accounts_user_id_key"
            )
        )
        await conn.execute(
            text("DROP INDEX IF EXISTS ix_paper_accounts_user_id")
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_paper_accounts_user_id "
                "ON paper_accounts (user_id)"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE paper_trade_settings "
                "DROP CONSTRAINT IF EXISTS paper_trade_settings_user_id_key"
            )
        )
        await conn.execute(
            text("DROP INDEX IF EXISTS ix_paper_trade_settings_user_id")
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_paper_trade_settings_user_id "
                "ON paper_trade_settings (user_id)"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE paper_accounts "
                "DROP CONSTRAINT IF EXISTS uq_paper_accounts_user_mode"
            )
        )
        for ddl in (
            "ALTER TABLE paper_accounts "
            "ADD CONSTRAINT uq_paper_accounts_user_mode "
            "UNIQUE (user_id, trading_mode)",
            "ALTER TABLE paper_trade_settings "
            "DROP CONSTRAINT IF EXISTS paper_trade_settings_user_id_key",
            "ALTER TABLE paper_trade_settings "
            "DROP CONSTRAINT IF EXISTS uq_paper_trade_settings_user_mode",
            "ALTER TABLE paper_trade_settings "
            "ADD CONSTRAINT uq_paper_trade_settings_user_mode "
            "UNIQUE (user_id, trading_mode)",
            "ALTER TABLE watchlist "
            "DROP CONSTRAINT IF EXISTS uq_user_contract",
            "ALTER TABLE watchlist "
            "DROP CONSTRAINT IF EXISTS uq_user_mode_contract",
            "ALTER TABLE watchlist "
            "ADD CONSTRAINT uq_user_mode_contract "
            "UNIQUE (user_id, trading_mode, contract_symbol)",
            # 持仓/委托来源 source 字段 + 持仓唯一约束改造（加 source）
            "ALTER TABLE paper_positions "
            "ADD COLUMN IF NOT EXISTS source VARCHAR(16) DEFAULT 'manual'",
            "ALTER TABLE paper_orders "
            "ADD COLUMN IF NOT EXISTS source VARCHAR(16) DEFAULT 'manual'",
            "ALTER TABLE paper_positions "
            "DROP CONSTRAINT IF EXISTS uq_paper_pos_account_symbol_dir",
            "ALTER TABLE paper_positions "
            "ADD CONSTRAINT uq_paper_pos_account_symbol_dir "
            "UNIQUE (account_id, symbol, direction, source)",
            # 任务级持仓隔离：加 task_id 列 + 重建唯一约束含 COALESCE(task_id)
            "ALTER TABLE paper_positions "
            "ADD COLUMN IF NOT EXISTS task_id UUID",
            "ALTER TABLE paper_orders "
            "ADD COLUMN IF NOT EXISTS task_id UUID",
            "CREATE INDEX IF NOT EXISTS ix_paper_positions_task_id "
            "ON paper_positions (task_id)",
            "CREATE INDEX IF NOT EXISTS ix_paper_orders_task_id "
            "ON paper_orders (task_id)",
            "ALTER TABLE paper_positions "
            "DROP CONSTRAINT IF EXISTS uq_paper_pos_account_symbol_dir_task",
            # 用唯一索引（非约束）支持 COALESCE 表达式：NULL task_id 手动单聚合
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_paper_pos_task_isolation "
            "ON paper_positions (account_id, symbol, direction, source, "
            "COALESCE(task_id, '00000000-0000-0000-0000-000000000000'))",
            # 消息提示音开关
            "ALTER TABLE user_notify_settings "
            "ADD COLUMN IF NOT EXISTS notify_sound_enabled BOOLEAN "
            "NOT NULL DEFAULT true",
            # 存量持仓来源回填：按品种关联任务类型更正（幂等，仅 source=manual 才改）
            "UPDATE paper_positions SET source = 'ai' "
            "WHERE source = 'manual' AND EXISTS ("
            "SELECT 1 FROM ai_trading_tasks t "
            "WHERE t.user_id = paper_positions.user_id "
            "AND t.symbol = paper_positions.symbol "
            "AND t.strategy_type = 'ai')",
            "UPDATE paper_positions SET source = 'quant' "
            "WHERE source = 'manual' AND EXISTS ("
            "SELECT 1 FROM ai_trading_tasks t "
            "WHERE t.user_id = paper_positions.user_id "
            "AND t.symbol = paper_positions.symbol "
            "AND t.strategy_type <> 'ai')",
            # factor_mining_tasks 数值列类型修正：初版误用 VARCHAR，改为 REAL。
            # USING 子句把已有字符串数据转成 double precision，幂等（已是 REAL 时 try 兜住）。
            "ALTER TABLE factor_mining_tasks "
            "ALTER COLUMN cost TYPE REAL USING cost::double precision",
            "ALTER TABLE factor_mining_tasks "
            "ALTER COLUMN train_ratio TYPE REAL USING train_ratio::double precision",
            "ALTER TABLE factor_mining_tasks "
            "ALTER COLUMN best_composite TYPE REAL USING best_composite::double precision",
            # 补 created_at 列（初版模型遗漏，列表按此排序）
            "ALTER TABLE factor_mining_tasks "
            "ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW()",
            # 实盘口径门开关（P1：测试段次根开盘成交 sortino>0 才晋级）
            "ALTER TABLE factor_mining_tasks "
            "ADD COLUMN IF NOT EXISTS live_fill_gate BOOLEAN "
            "NOT NULL DEFAULT FALSE",
            # 跨品种验证开关（P6：≥半数同板块伙伴品种 sortino>0 才晋级）
            "ALTER TABLE factor_mining_tasks "
            "ADD COLUMN IF NOT EXISTS cross_validate BOOLEAN "
            "NOT NULL DEFAULT FALSE",
            # 岛模型岛数（P7：1=关闭）
            "ALTER TABLE factor_mining_tasks "
            "ADD COLUMN IF NOT EXISTS islands INTEGER NOT NULL DEFAULT 1",
            # 因子衰减监控（Q1：250 根滚动 IC 口径，E10b 验证前瞻差 -0.31）
            "ALTER TABLE factor_favorites "
            "ADD COLUMN IF NOT EXISTS health JSONB",
            # 成交量分布：四路开平拆分（估算，方向不明/持仓缺失时 NULL）
            "ALTER TABLE trade_tick "
            "ADD COLUMN IF NOT EXISTS open_long INTEGER",
            "ALTER TABLE trade_tick "
            "ADD COLUMN IF NOT EXISTS close_long INTEGER",
            "ALTER TABLE trade_tick "
            "ADD COLUMN IF NOT EXISTS open_short INTEGER",
            "ALTER TABLE trade_tick "
            "ADD COLUMN IF NOT EXISTS close_short INTEGER",
        ):
            try:
                await conn.execute(text(ddl))
            except Exception:
                # 约束已存在或名称因环境不同略有差异
                pass


async def _start_stock_eod(application: FastAPI, settings, logger) -> None:
    application.state.stock_eod_worker = None
    application.state.stock_eod_enabled = False
    application.state.stock_eod_sync_enabled = bool(getattr(settings, "stock_eod_sync_enabled", False))
    application.state.stock_eod_catchup_enabled = bool(getattr(settings, "stock_eod_catchup_enabled", False))
    application.state.stock_eod_error_code = None
    if not getattr(settings, "stock_eod_enabled", False):
        if getattr(settings, "stock_eod_sync_enabled", False):
            application.state.stock_eod_error_code = "eod_read_disabled"
            logger.warning("operation=stock_eod_start error_code=eod_read_disabled; worker not started")
        return
    try:
        async with asyncio.timeout(10):
            await migrate_stock_eod(application.state.engine, check=True)
    except Exception as error:
        application.state.stock_eod_error_code = "eod_migration_not_ready"
        logger.error("operation=stock_eod_readiness error_code=eod_migration_not_ready error_type=%s", type(error).__name__)
        return
    application.state.stock_eod_enabled = True
    if not getattr(settings, "stock_eod_sync_enabled", False):
        return
    token = getattr(settings, "tushare_token", "").strip()
    if not token:
        application.state.stock_eod_error_code = "eod_source_not_configured"
        logger.warning("operation=stock_eod_start error_code=eod_source_not_configured; worker not started")
        return
    worker = None
    try:
        worker = StockEodWorker(application.state.session_factory, TeajoinEodSource(token), catchup_enabled=application.state.stock_eod_catchup_enabled)
        worker.start()
        application.state.stock_eod_worker = worker
    except Exception as error:
        application.state.stock_eod_error_code = "eod_worker_start_failed"
        logger.error("operation=stock_eod_start error_code=eod_worker_start_failed error_type=%s", type(error).__name__)
        if worker is not None:
            try:
                async with asyncio.timeout(15):
                    await worker.stop()
            except Exception as cleanup_error:
                logger.error("operation=stock_eod_start_cleanup error_type=%s", type(cleanup_error).__name__)


async def _start_stock_archive(application: FastAPI, settings, logger) -> None:
    application.state.stock_archive_enabled = bool(getattr(settings, "stock_daily_archive_enabled", False))
    application.state.stock_archive_ready = False
    application.state.stock_archive_error_code = None
    application.state.stock_archive_worker = None
    if not application.state.stock_archive_enabled:
        return
    try:
        async with asyncio.timeout(10):
            await migrate_stock_eod(application.state.engine, check=True)
            await migrate_stock_daily_archive(application.state.engine, check=True)
            await migrate_stock_archive_jobs(application.state.engine, check=True)
    except Exception as error:
        application.state.stock_archive_error_code = "archive_migration_not_ready"
        logger.error("operation=stock_archive_readiness error_type=%s", type(error).__name__)
        return
    worker = None
    try:
        worker = StockArchiveWorker(application.state.session_factory)
        worker.start()
        application.state.stock_archive_worker = worker
        application.state.stock_archive_ready = True
    except Exception as error:
        application.state.stock_archive_error_code = "archive_worker_start_failed"
        logger.error("operation=stock_archive_start error_type=%s", type(error).__name__)
        if worker is not None:
            try:
                async with asyncio.timeout(15):
                    await worker.stop()
            except Exception as cleanup_error:
                logger.error("operation=stock_archive_start_cleanup error_type=%s", type(cleanup_error).__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    """应用生命周期：启动和关闭时管理数据库、Redis、APScheduler"""
    settings = load_settings()
    logger = setup_logger(settings.log_level)

    # 启动：建立连接
    logger.info("正在启动应用...")
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with asyncio.timeout(15):
            schema_status = await verify_database_schema(engine)
    except Exception as error:
        await engine.dispose()
        logger.error("operation=database_startup_readiness error_type=%s", type(error).__name__)
        raise
    logger.info("只读数据库接口检查通过 tables=%s columns=%s validation=%s；启动不会执行DDL或数据修补", schema_status["tables_checked"], schema_status["columns_checked"], schema_status["validation"])
    redis_conn = await create_redis_connection(settings)

    application.state.engine = engine
    application.state.session_factory = session_factory
    application.state.redis = redis_conn

    # 注入会话工厂供模拟挂单撮合使用，再启动定时任务
    set_session_factory(session_factory)
    await start_scheduler(redis_conn)
    application.state.stock_research_worker = None
    await start_gp_worker(application, session_factory, settings)
    if settings.stock_research_jobs_enabled:
        async with session_factory() as session:
            if await research_ready(session, settings):
                application.state.stock_research_worker = StockResearchWorker(session_factory)
                application.state.stock_research_worker.start()
            else:
                logger.warning("stock_research_jobs enabled but version migration is not ready; worker not started")

    # 启动 WS 通知跨 worker 订阅（Redis pub/sub → 本进程 _clients）
    await start_notify_subscriber(redis_conn)
    # 启动 WS 大单事件跨 worker 订阅（按 trading_mode 分频道）
    await start_big_order_subscriber(redis_conn)
    # 启动 WS 运维告警跨 worker 订阅（推在线 admin）
    await start_ops_alert_subscriber(redis_conn)
    await _start_stock_eod(application, settings, logger)
    await _start_stock_archive(application, settings, logger)

    logger.info("应用启动完成，数据库、Redis、定时任务已就绪")

    try:
        yield
    finally:
        logger.info("正在关闭应用...")
        if application.state.stock_archive_worker is not None:
            try:
                async with asyncio.timeout(15):
                    await application.state.stock_archive_worker.stop()
            except Exception as error:
                logger.error("operation=stock_archive_stop error_type=%s", type(error).__name__)
        if application.state.stock_eod_worker is not None:
            try:
                async with asyncio.timeout(15):
                    await application.state.stock_eod_worker.stop()
            except Exception as error:
                logger.error("operation=stock_eod_stop error_type=%s", type(error).__name__)
        if application.state.stock_research_worker is not None:
            await application.state.stock_research_worker.stop()
        if getattr(application.state, "gp_checkpoint_worker", None) is not None:
            await application.state.gp_checkpoint_worker.stop()
        await stop_ops_alert_subscriber()
        await stop_big_order_subscriber()
        await stop_notify_subscriber()
        await stop_scheduler()
        await close_redis_connection(redis_conn)
        await engine.dispose()
        logger.info("应用已关闭")


def create_app() -> FastAPI:
    """创建 FastAPI 应用实例"""
    application = FastAPI(
        title="期货交易系统",
        version="0.1.0",
        lifespan=lifespan,
    )

    # 开发环境 CORS
    import os

    _base_origins = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    # 对外展示站域名（逗号分隔），由 PUBLIC_API_CORS_ORIGINS 注入
    _extra_origins = [
        o.strip()
        for o in os.getenv("PUBLIC_API_CORS_ORIGINS", "").split(",")
        if o.strip()
    ]
    application.add_middleware(
        CORSMiddleware,
        allow_origins=_base_origins + _extra_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 注册路由
    application.include_router(health_router)
    application.include_router(auth_router)
    application.include_router(users_router)
    application.include_router(kline_router)
    application.include_router(market_router)
    application.include_router(contracts_router)
    application.include_router(news_router)
    application.include_router(watchlist_router)
    application.include_router(paper_router)
    application.include_router(ai_trading_router)
    application.include_router(ai_anchor_router)
    application.include_router(strategy_folders_router)
    application.include_router(ai_trading_favorites_router)
    application.include_router(backtest_router)
    application.include_router(admin_router)
    application.include_router(ws_router)
    application.include_router(ai_provider_router)
    application.include_router(ai_model_router)
    application.include_router(ai_chat_router)
    application.include_router(ai_conversation_router)
    application.include_router(ai_knowledge_router)
    application.include_router(ai_memory_router)
    application.include_router(ai_skills_router)
    application.include_router(ai_tools_router)
    application.include_router(notifications_router)
    application.include_router(price_alerts_router)
    application.include_router(big_orders_router)
    application.include_router(task_alerts_router)
    application.include_router(showcase_router)
    application.include_router(public_router)
    application.include_router(admin_api_keys_router)
    application.include_router(factor_lab_router)
    application.include_router(factor_mining_router)
    application.include_router(stock_router)
    application.include_router(stock_market_router)
    application.include_router(stock_replay_router)
    application.include_router(stock_factors_router)
    application.include_router(stock_research_jobs_router)
    application.include_router(stock_research_reports_router)
    application.include_router(gp_jobs_router)
    application.include_router(indicators_router)

    return application


app = create_app()
