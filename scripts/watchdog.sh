#!/usr/bin/env bash
# 服务看门狗 —— 每 15s 检查并拉起：PostgreSQL / Redis(WSL) / backend / sina-bridge / frontend
# 用法：bash scripts/watchdog.sh        （Ctrl+C 停止）
# 背景跑：bash scripts/watchdog.sh &    （输出重定向到 logs/watchdog.log）

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PG_BIN="C:/Program Files/PostgreSQL/17/bin"
PG_DATA="F:/qihuo-pg-data"
BACKEND_DIR="$PROJECT_ROOT/backend"
FRONTEND_DIR="$PROJECT_ROOT/frontend"
LOG_DIR="$PROJECT_ROOT/logs"
PY="$BACKEND_DIR/.venv/Scripts/python.exe"
mkdir -p "$LOG_DIR"

port_open() { netstat -ano 2>/dev/null | grep -q "TCP.*:$1 .*LISTENING"; }

redis_ping() {
    [ -n "${1:-}" ] || return 1
    "$PY" -c "import redis,sys; sys.exit(0 if redis.Redis(host='$1',port=6380).ping() else 1)" 2>/dev/null
}

log() { echo "[$(date '+%H:%M:%S')] $*"; }

log "看门狗启动（15s 巡检）"

# 维护模式：logs/.maintenance 存在时不拉起 frontend/backend（数据导入等重负载期）
maintenance() { [ -f "$LOG_DIR/.maintenance" ]; }

while true; do
    # 1. PostgreSQL (5433)
    if ! port_open 5433; then
        "$PG_BIN/pg_ctl.exe" -D "$PG_DATA" -l "$PG_DATA/postgres.log" start >/dev/null 2>&1
        sleep 2
        port_open 5433 && log "PostgreSQL 已拉起" || log "PostgreSQL 拉起失败"
    fi

    # 2. Redis (WSL，VM IP 直连)
    WSL_IP=$(wsl -d Ubuntu-24.04 -u root bash -c "hostname -I | awk '{print \$1}'" 2>/dev/null | tr -d ' \r\n')
    if ! redis_ping "$WSL_IP"; then
        wsl -d Ubuntu-24.04 -u root bash -c "redis-server /etc/redis/redis.conf --daemonize yes" >/dev/null 2>&1
        sleep 2
        if redis_ping "$WSL_IP"; then
            log "Redis 已拉起（$WSL_IP:6380）"
            if ! grep -q "^REDIS_HOST=$WSL_IP$" "$PROJECT_ROOT/.env"; then
                sed -i "s|^REDIS_HOST=.*|REDIS_HOST=$WSL_IP|" "$PROJECT_ROOT/.env"
                log ".env REDIS_HOST → $WSL_IP（需重启 backend 生效）"
            fi
        else
            log "Redis 拉起失败"
        fi
    fi

    # 3. backend (8002)
    if ! port_open 8002 && ! maintenance; then
        (cd "$BACKEND_DIR" && set -a && . "$PROJECT_ROOT/.env" && set +a && \
         "$PY" -m uvicorn app.main:app --host 0.0.0.0 --port 8002 >> "$LOG_DIR/backend.log" 2>&1 &)
        sleep 12
        port_open 8002 && log "backend 已拉起" || log "backend 拉起失败（见 logs/backend.log）"
    fi

    # 4. sina-bridge (8110)
    if ! port_open 8110; then
        (cd "$BACKEND_DIR" && set -a && . "$PROJECT_ROOT/.env" && set +a && \
         "$PY" -m app.connectors.sina.main >> "$LOG_DIR/sina-bridge.log" 2>&1 &)
        sleep 6
        port_open 8110 && log "sina-bridge 已拉起" || log "sina-bridge 拉起失败"
    fi

    # 5. frontend (3000)
    if ! port_open 3000 && ! maintenance; then
        (cd "$FRONTEND_DIR" && pnpm dev >> "$LOG_DIR/frontend.log" 2>&1 &)
        sleep 12
        port_open 3000 && log "frontend 已拉起" || log "frontend 拉起失败"
    fi

    sleep 15
done
