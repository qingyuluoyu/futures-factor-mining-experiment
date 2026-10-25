#!/usr/bin/env bash
# 启动前端开发服务器 —— Next.js 热重载

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
FRONTEND_DIR="$PROJECT_ROOT/frontend"

if [ ! -f "$FRONTEND_DIR/package.json" ]; then
    echo "[!] 前端工程不存在，请先运行: bash scripts/setup.sh"
    exit 1
fi

echo "[→] 启动前端开发服务器..."
cd "$FRONTEND_DIR"
pnpm dev
