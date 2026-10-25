#!/usr/bin/env bash
# 本地裸机一键启动（不用 Docker）：PostgreSQL + Redis(WSL) + backend + sina-bridge + frontend
# 用法：bash scripts/run-local.sh          （已在运行的服务会跳过）
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

# Redis 探活（WSL IP 直连，比 localhost 转发稳定）
redis_ping() {
    [ -n "$1" ] || return 1
    "$BACKEND_DIR/.venv/Scripts/python.exe" -c "import redis,sys; sys.exit(0 if redis.Redis(host='$1',port=6380).ping() else 1)" 2>/dev/null
}

echo "==> [1/5] PostgreSQL (127.0.0.1:5433)"
if port_open 5433; then
    echo "    已在运行"
elif "$PG_BIN/pg_ctl.exe" -D "$PG_DATA" status >/dev/null 2>&1; then
    "$PG_BIN/pg_ctl.exe" -D "$PG_DATA" -l "$PG_DATA/postgres.log" start >/dev/null
    echo "    已启动"
else
    echo "    [!] 集群未初始化：请先 initdb（见 README 或询问维护者）"
    exit 1
fi

echo "==> [2/5] Redis (WSL Ubuntu, 端口 6380)"
WSL_IP=$(wsl -d Ubuntu-24.04 -u root bash -c "hostname -I | awk '{print \$1}'" 2>/dev/null | tr -d ' 
')
if redis_ping "$WSL_IP"; then
    echo "    已在运行（$WSL_IP:6380）"
else
    wsl -d Ubuntu-24.04 -u root bash -c "redis-server /etc/redis/redis.conf --daemonize yes" 2>/dev/null
    sleep 2
    redis_ping "$WSL_IP" && echo "    已启动（$WSL_IP:6380）" || echo "    [!] 启动失败，请检查 WSL redis"
fi
# .env 的 REDIS_HOST 同步为当前 WSL IP（WSL 重启后 IP 会变）
if [ -n "$WSL_IP" ] && ! grep -q "^REDIS_HOST=$WSL_IP$" "$PROJECT_ROOT/.env"; then
    sed -i "s|^REDIS_HOST=.*|REDIS_HOST=$WSL_IP|" "$PROJECT_ROOT/.env"
    echo "    .env REDIS_HOST 已更新为 $WSL_IP"
fi

echo "==> [3/5] backend (0.0.0.0:8002)"
if port_open 8002; then
    echo "    已在运行"
else
    (cd "$BACKEND_DIR" && set -a && . "$PROJECT_ROOT/.env" && set +a && \
     "$PY" -m uvicorn app.main:app --host 0.0.0.0 --port 8002 >> "$LOG_DIR/backend.log" 2>&1 &)
    sleep 8
    port_open 8002 && echo "    已启动（日志 logs/backend.log）" || echo "    [!] 启动失败，查看 logs/backend.log"
fi

echo "==> [4/5] sina-bridge (0.0.0.0:8110)"
if port_open 8110; then
    echo "    已在运行"
else
    (cd "$BACKEND_DIR" && set -a && . "$PROJECT_ROOT/.env" && set +a && \
     "$PY" -m app.connectors.sina.main >> "$LOG_DIR/sina-bridge.log" 2>&1 &)
    sleep 5
    port_open 8110 && echo "    已启动（日志 logs/sina-bridge.log）" || echo "    [!] 启动失败，查看 logs/sina-bridge.log"
fi

echo "==> [5/5] frontend (0.0.0.0:3000)"
if port_open 3000; then
    echo "    已在运行"
else
    (cd "$FRONTEND_DIR" && pnpm dev >> "$LOG_DIR/frontend.log" 2>&1 &)
    sleep 10
    port_open 3000 && echo "    已启动（日志 logs/frontend.log）" || echo "    [!] 启动失败，查看 logs/frontend.log"
fi

echo
echo "完成。访问："
echo "  前端     http://127.0.0.1:3000      （演示账号 dazhu / qihuo123456，登录选「虚拟盘」）"
echo "  后端API  http://127.0.0.1:8002/docs"
echo "  行情桥   http://127.0.0.1:8110"
