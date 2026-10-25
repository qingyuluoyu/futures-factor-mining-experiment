#!/usr/bin/env bash
# 启动后端开发服务器 —— uvicorn 热重载

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$PROJECT_ROOT/backend"

if [ ! -d "$BACKEND_DIR/.venv" ]; then
    echo "[!] 后端虚拟环境不存在，请先运行: bash scripts/setup.sh"
    exit 1
fi

mkdir -p "$PROJECT_ROOT/logs"

echo "[→] 启动后端开发服务器..."
cd "$BACKEND_DIR"
uv run uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8002 \
    --reload \
    --reload-dir app
