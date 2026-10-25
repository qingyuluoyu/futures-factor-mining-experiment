#!/usr/bin/env bash
# 启动 / 停止 SimNow CTP 行情桥（独立进程，写 Redis market:quote:*）
# 用法:
#   bash scripts/ctp-md-bridge.sh start
#   bash scripts/ctp-md-bridge.sh stop
#   bash scripts/ctp-md-bridge.sh status
#
# 依赖 .env 中:
#   SIMNOW_BROKER_ID SIMNOW_INVESTOR_ID SIMNOW_PASSWORD SIMNOW_MD_FRONT
#   REDIS_HOST REDIS_PORT REDIS_DB
# 可选: openctp-ctp（在 backend .venv 中安装）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$PROJECT_ROOT/backend"
PID_FILE="${PROJECT_ROOT}/logs/ctp-md-bridge.pid"
LOG_FILE="${PROJECT_ROOT}/logs/ctp-md-bridge.log"

mkdir -p "$PROJECT_ROOT/logs"

# 加载根目录 .env（不 export 注释行）
if [[ -f "$PROJECT_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_ROOT/.env"
  set +a
fi

_run_python() {
  cd "$BACKEND_DIR"
  if [[ -x "$BACKEND_DIR/.venv/Scripts/python.exe" ]]; then
    "$BACKEND_DIR/.venv/Scripts/python.exe" -m app.connectors.ctp.md_bridge
  elif [[ -x "$BACKEND_DIR/.venv/bin/python" ]]; then
    "$BACKEND_DIR/.venv/bin/python" -m app.connectors.ctp.md_bridge
  else
    uv run python -m app.connectors.ctp.md_bridge
  fi
}

cmd_start() {
  if [[ -f "$PID_FILE" ]]; then
    old="$(cat "$PID_FILE" || true)"
    if [[ -n "${old}" ]] && kill -0 "$old" 2>/dev/null; then
      echo "ctp-md-bridge 已在运行 pid=$old"
      exit 0
    fi
  fi
  if [[ -z "${SIMNOW_PASSWORD:-}" ]]; then
    echo "错误: 请在 .env 设置 SIMNOW_PASSWORD"
    exit 1
  fi
  if [[ -z "${SIMNOW_MD_FRONT:-}" ]]; then
    echo "错误: 请在 .env 设置 SIMNOW_MD_FRONT（以 SimNow 官网当前前置为准）"
    exit 1
  fi
  echo "启动 ctp-md-bridge → $LOG_FILE"
  (
    cd "$BACKEND_DIR"
    nohup bash -c "_run(){ if [ -x .venv/bin/python ]; then .venv/bin/python -m app.connectors.ctp.md_bridge; elif [ -x .venv/Scripts/python.exe ]; then .venv/Scripts/python.exe -m app.connectors.ctp.md_bridge; else uv run python -m app.connectors.ctp.md_bridge; fi; }; _run" \
      >>"$LOG_FILE" 2>&1 &
    echo $! >"$PID_FILE"
  )
  echo "已启动 pid=$(cat "$PID_FILE")"
  echo "管理端可将「实时行情渠道」切换为 simnow"
}

cmd_stop() {
  if [[ ! -f "$PID_FILE" ]]; then
    echo "无 pid 文件，可能未运行"
    exit 0
  fi
  pid="$(cat "$PID_FILE")"
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" || true
    sleep 1
    kill -9 "$pid" 2>/dev/null || true
    echo "已停止 pid=$pid"
  else
    echo "进程不存在 pid=$pid"
  fi
  rm -f "$PID_FILE"
}

cmd_status() {
  if [[ -f "$PID_FILE" ]]; then
    pid="$(cat "$PID_FILE")"
    if kill -0 "$pid" 2>/dev/null; then
      echo "running pid=$pid"
      exit 0
    fi
  fi
  echo "stopped"
}

case "${1:-}" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  status) cmd_status ;;
  *)
    echo "用法: $0 {start|stop|status}"
    exit 1
    ;;
esac
