#!/usr/bin/env bash
# A股数据导入循环 —— 崩溃/超时自动重启续跑，直到股票池全覆盖
# 用法：bash scripts/import-stock-loop.sh [--universe hs300] [--start-date 20180101]
# 说明：本机内存紧张时导入进程可能被系统杀掉；本脚本靠增量设计反复续跑直至完成。

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PY="$PROJECT_ROOT/backend/.venv/Scripts/python.exe"
PSQL="C:/Program Files/PostgreSQL/17/bin/psql.exe"
LOG="$PROJECT_ROOT/logs/import-loop.log"

ARGS=("$@")
MAX_ROUNDS=40

stock_codes() {
    "$PSQL" -h 127.0.0.1 -p 5433 -U qihuo -d qihuo -t -A \
        -c "SELECT count(*) FROM stock_instruments;" 2>/dev/null | tr -d ' \r\n'
}

imported_codes() {
    "$PSQL" -h 127.0.0.1 -p 5433 -U qihuo -d qihuo -t -A \
        -c "SELECT count(DISTINCT code) FROM kline_history WHERE market_type='stock';" 2>/dev/null | tr -d ' \r\n'
}

TOTAL=$(stock_codes)
echo "[$(date '+%H:%M:%S')] 股票池 $TOTAL 只，开始循环导入" | tee -a "$LOG"

for round in $(seq 1 $MAX_ROUNDS); do
    DONE=$(imported_codes)
    echo "[$(date '+%H:%M:%S')] 第 $round 轮：已导入 $DONE / $TOTAL 只" | tee -a "$LOG"
    if [ -n "$DONE" ] && [ -n "$TOTAL" ] && [ "$DONE" -ge "$TOTAL" ]; then
        echo "[$(date '+%H:%M:%S')] 全部完成！" | tee -a "$LOG"
        exit 0
    fi
    # 单轮限时 8 分钟，超时/崩溃都由下一轮续跑
    timeout 480 "$PY" -u "$PROJECT_ROOT/scripts/import-stock-data.py" "${ARGS[@]}" \
        >> "$LOG" 2>&1
    code=$?
    echo "[$(date '+%H:%M:%S')] 第 $round 轮退出码 $code，5 秒后继续" | tee -a "$LOG"
    sleep 5
done

echo "[$(date '+%H:%M:%S')] 达到最大轮数，当前进度 $(imported_codes) / $TOTAL" | tee -a "$LOG"
exit 1
