#!/usr/bin/env bash
# 前端生产构建 —— next build（含 TypeScript 类型检查）
# 用于推送前本地验证类型与构建，避免把类型错误留到 33 的镜像构建阶段才暴露。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
FRONTEND_DIR="$PROJECT_ROOT/frontend"
LOG_DIR="$PROJECT_ROOT/logs"
LOG_FILE="$LOG_DIR/frontend-build.log"

if [ ! -f "$FRONTEND_DIR/package.json" ]; then
    echo "[!] 前端工程不存在，请先运行: bash scripts/setup.sh"
    exit 1
fi

mkdir -p "$LOG_DIR"

echo "[→] 前端构建中（日志: $LOG_FILE）..."
cd "$FRONTEND_DIR"
if pnpm build 2>&1 | tee "$LOG_FILE"; then
    echo "[✓] 前端构建通过"
else
    echo "[✗] 前端构建失败，详见 $LOG_FILE"
    exit 1
fi
