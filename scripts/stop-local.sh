#!/usr/bin/env bash
# 本地裸机一键停止（不用 Docker）
set -uo pipefail

echo "==> 停止 frontend (3000)"
for pid in $(netstat -ano 2>/dev/null | grep "TCP.*:3000 .*LISTENING" | awk '{print $NF}' | sort -u); do
    taskkill //PID "$pid" //T //F >/dev/null 2>&1 && echo "    killed PID $pid"
done

echo "==> 停止 sina-bridge (8110)"
for pid in $(netstat -ano 2>/dev/null | grep "TCP.*:8110 .*LISTENING" | awk '{print $NF}' | sort -u); do
    taskkill //PID "$pid" //T //F >/dev/null 2>&1 && echo "    killed PID $pid"
done

echo "==> 停止 backend (8002)"
for pid in $(netstat -ano 2>/dev/null | grep "TCP.*:8002 .*LISTENING" | awk '{print $NF}' | sort -u); do
    taskkill //PID "$pid" //T //F >/dev/null 2>&1 && echo "    killed PID $pid"
done

echo "==> 停止 Redis (WSL 6380)"
wsl -d Ubuntu-24.04 -u root bash -c "redis-cli -p 6380 shutdown nosave 2>/dev/null" 2>/dev/null
echo "    done"

echo "==> 停止 PostgreSQL (5433)"
"C:/Program Files/PostgreSQL/17/bin/pg_ctl.exe" -D "F:/qihuo-pg-data" stop -m fast >/dev/null 2>&1
echo "    done"

echo "全部已停止。"
