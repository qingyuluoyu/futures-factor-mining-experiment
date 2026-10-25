#!/usr/bin/env bash
# 初始化数据库表结构

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$PROJECT_ROOT/backend"

if [ ! -d "$BACKEND_DIR/.venv" ]; then
    echo "[!] 后端虚拟环境不存在，请先运行: bash scripts/setup.sh"
    exit 1
fi

echo "[→] 初始化数据库..."
cd "$BACKEND_DIR"

# 使用 Python 脚本执行数据库初始化
uv run python -c "
import asyncio
from sqlalchemy import text
from app.core.config import load_settings
from app.core.database import create_database_engine
from app.core.logger import setup_logger

async def init_db():
    settings = load_settings()
    logger = setup_logger(settings.log_level)
    engine = create_database_engine(settings)
    try:
        async with engine.begin() as conn:
            # 验证数据库连接
            result = await conn.execute(text('SELECT 1'))
            logger.info('数据库连接成功')
    finally:
        await engine.dispose()

asyncio.run(init_db())
"

echo "[✓] 数据库初始化完成"
