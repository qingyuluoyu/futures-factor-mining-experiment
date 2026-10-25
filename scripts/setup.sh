#!/usr/bin/env bash
# 一键初始化开发环境 —— 创建 .venv、安装依赖、创建 logs/ 目录

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== 期货交易系统 - 开发环境初始化 ==="
echo "项目根目录: $PROJECT_ROOT"

# 创建 logs/ 目录
mkdir -p "$PROJECT_ROOT/logs"
echo "[✓] logs/ 目录已创建"

# 后端：创建 .venv 并安装依赖
BACKEND_DIR="$PROJECT_ROOT/backend"
if [ ! -d "$BACKEND_DIR/.venv" ]; then
    echo "[→] 正在创建后端 Python 虚拟环境..."
    cd "$BACKEND_DIR"
    uv venv .venv
    echo "[✓] .venv 已创建"
else
    echo "[✓] .venv 已存在，跳过创建"
fi

echo "[→] 正在安装后端依赖..."
cd "$BACKEND_DIR"
uv sync
echo "[✓] 后端依赖安装完成"

# 前端：安装依赖
FRONTEND_DIR="$PROJECT_ROOT/frontend"
if [ -f "$FRONTEND_DIR/package.json" ]; then
    echo "[→] 正在安装前端依赖..."
    cd "$FRONTEND_DIR"
    pnpm install
    echo "[✓] 前端依赖安装完成"
else
    echo "[!] 前端工程尚未创建，请先运行 create-next-app"
fi

echo ""
echo "=== 初始化完成 ==="
