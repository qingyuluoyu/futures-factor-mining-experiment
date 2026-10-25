"""运行日志导出 —— CSV 展平关键字段 / JSON 全量（StreamingResponse）

CSV 行结构（detail 展平为参考价值最高的字段）：
  ts, task_id, symbol, strategy_type, level, event, action, order_id,
  bar_time, reason, factor_position, factor_text, model_action, last_price
JSON 为 run_log 行原样全量（detail 含因子快照/模型输出/账户状态）。
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_trading import AITradingRunLog, AITradingTask

_CSV_COLUMNS = [
    "ts", "task_id", "symbol", "strategy_type", "level", "event",
    "action", "order_id", "bar_time", "reason",
    "factor_position", "factor_text", "model_action", "last_price",
]


def _row_to_flat(r: AITradingRunLog) -> dict[str, Any]:
    detail = r.detail if isinstance(r.detail, dict) else {}
    factor = detail.get("factor") if isinstance(detail.get("factor"), dict) else {}
    signal = factor.get("signal") if isinstance(factor.get("signal"), dict) else {}
    model = detail.get("model_output") if isinstance(detail.get("model_output"), dict) else {}
    return {
        "ts": r.created_at.isoformat() if r.created_at else "",
        "task_id": str(r.task_id),
        "symbol": r.symbol or "",
        "strategy_type": r.strategy_type or "",
        "level": r.level,
        "event": r.event,
        "action": r.action or "",
        "order_id": str(r.order_id) if r.order_id else "",
        "bar_time": r.bar_time or "",
        "reason": (r.reason or "").replace("\n", " "),
        "factor_position": signal.get("position", ""),
        "factor_text": signal.get("text", ""),
        "model_action": model.get("action", ""),
        "last_price": detail.get("last_price", ""),
    }


def _row_to_full(r: AITradingRunLog) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "task_id": str(r.task_id),
        "user_id": str(r.user_id),
        "bar_time": r.bar_time,
        "timeframe": r.timeframe,
        "symbol": r.symbol,
        "strategy_type": r.strategy_type,
        "side_mode": r.side_mode,
        "level": r.level,
        "event": r.event,
        "action": r.action,
        "order_id": str(r.order_id) if r.order_id else None,
        "reason": r.reason,
        "detail": r.detail if isinstance(r.detail, dict) else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


async def fetch_export_rows(
    session: AsyncSession,
    *,
    user_id: UUID,
    task_id: UUID | None,
    level: str | None,
    event: str | None,
    trading_mode: str = "live",
    limit: int = 5000,
) -> list[AITradingRunLog]:
    """导出用取数（时间倒序，上限 limit 防滥用）"""
    filters = [
        AITradingRunLog.user_id == user_id,
        AITradingTask.trading_mode == trading_mode,
    ]
    if task_id is not None:
        filters.append(AITradingRunLog.task_id == task_id)
    if level:
        filters.append(AITradingRunLog.level == level)
    if event:
        filters.append(AITradingRunLog.event == event)
    stmt = (
        select(AITradingRunLog)
        .join(AITradingTask, AITradingTask.id == AITradingRunLog.task_id)
        .where(*filters)
        .order_by(AITradingRunLog.created_at.desc())
        .limit(max(1, min(limit, 20000)))
    )
    return list((await session.execute(stmt)).scalars().all())


def rows_to_csv(rows: list[AITradingRunLog]) -> str:
    buf = io.StringIO()
    # Excel 兼容：utf-8-sig BOM 由调用方在响应层加；此处纯 csv 文本
    w = csv.DictWriter(buf, fieldnames=_CSV_COLUMNS, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(_row_to_flat(r))
    return buf.getvalue()


def rows_to_json(rows: list[AITradingRunLog]) -> str:
    return json.dumps(
        [_row_to_full(r) for r in rows],
        ensure_ascii=False,
        indent=2,
        default=str,
    )
