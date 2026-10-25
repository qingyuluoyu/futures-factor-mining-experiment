#!/usr/bin/env bash
# 前端快速类型检查 —— tsc --noEmit
# 不依赖 next 运行时包，适合改动内环快速验证；
# 提交/推送前的完整验证仍以 frontend-build.sh（next build）为准。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
FRONTEND_DIR="$PROJECT_ROOT/frontend"

if [ ! -f "$FRONTEND_DIR/package.json" ]; then
    echo "[!] 前端工程不存在，请先运行: bash scripts/setup.sh"
    exit 1
fi

echo "[→] 前端类型检查（tsc --noEmit）..."
cd "$FRONTEND_DIR"
if pnpm exec tsc --noEmit; then
    echo "[✓] 类型检查通过"
else
    echo "[✗] 类型检查失败"
    exit 1
fi
