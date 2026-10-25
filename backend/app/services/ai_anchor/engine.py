"""AI 看盘主播引擎 —— 到期拉数 → 算指标 → 调模型 → 落播报 → WS 推送"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_anchor import AIAnchorBroadcast, AIAnchorTask
from app.services.ai_anchor.indicators import summarize_indicators
from app.services.ai_anchor.prompts import (
    build_system_prompt,
    build_user_prompt,
    parse_anchor_output,
)
from app.services.ai_anchor.svc import build_context_history, task_timeframes, utc_now
from app.services.factor_lab.llm.llm_http import chat_once
from app.services.kline import get_kline_history
from app.services.market_keys import get_quote_json
from app.services.trading_hours import is_symbol_trading

logger = logging.getLogger("qihuo.ai_anchor")

# 指标暖区根数：拉 bar_count + WARMUP 计算，模型只看最新 bar_count 根
WARMUP_BARS = 100
# 最少可用K线根数（低于此视为数据不足）
_MIN_BARS = 10
# 模型输出预算（思考类模型需要更大空间）
_MAX_TOKENS = 8192
_TEMPERATURE = 0.2


def anchor_market_open(task: AIAnchorTask) -> bool:
    """主播看盘品种当前是否交易中；virtual 盘 7×24 视为开市。"""
    if task.trading_mode == "virtual":
        return True
    return is_symbol_trading(task.symbol)


def _error_conclusion(reason: str) -> dict[str, Any]:
    """模型/数据失败时的降级观望结论。"""
    return {
        "direction": "neutral",
        "entry": None,
        "take_profit": None,
        "stop_loss": None,
        "action": "wait",
        "commentary": reason[:400],
        "position_advice": None,
        "parse_ok": False,
    }


def _position_pnl_points(direction: str, price: float, last: float) -> float:
    """浮动盈亏点数：多头 last-price，空头 price-last。"""
    diff = last - price
    return round(diff if direction == "long" else -diff, 2)


async def _build_position_info(
    task: AIAnchorTask,
    manual: Any,
    last_close: Any,
    redis_conn: Any,
) -> dict[str, Any] | None:
    """把手动持仓组装成 prompt 注入信息（含最新价/浮动盈亏点数）。

    持仓合约与看盘合约一致时直接用K线最新收盘价；
    否则读 Redis 报价，读不到则最新价/盈亏置空（播报提示最新价暂缺）。
    """
    if not isinstance(manual, dict):
        return None
    direction = str(manual.get("direction") or "long")
    if direction not in ("long", "short"):
        direction = "long"
    symbol = str(manual.get("symbol") or task.symbol).strip().lower()
    try:
        price = round(float(manual.get("price")), 4)
        quantity = float(manual.get("quantity"))
    except (TypeError, ValueError):
        return None
    if price <= 0 or quantity <= 0:
        return None
    # 最新价：持仓合约与看盘合约一致时用K线最新收盘价，否则从 Redis 报价取
    last: float | None = None
    if symbol == task.symbol:
        try:
            last = float(last_close)
        except (TypeError, ValueError):
            last = None
    if last is None and redis_conn is not None:
        quote = await get_quote_json(redis_conn, task.trading_mode, symbol)
        if quote:
            try:
                last = float(quote.get("last_price"))
            except (TypeError, ValueError):
                last = None
    return {
        "symbol": symbol,
        "direction": direction,
        "price": price,
        "quantity": quantity,
        "last": last,
        "pnl_points": _position_pnl_points(direction, price, last) if last is not None else None,
    }


def _trim_context_summary(
    summaries: list[dict[str, Any]],
    last_bar: dict[str, Any],
) -> dict[str, Any]:
    """落库的上下文摘要：只留指标名/信号/最新值，控制 JSONB 体积。"""
    indicators_trimmed = [
        {
            "name": item.get("name"),
            "params": item.get("params"),
            "signal": item.get("signal"),
            "latest": item.get("latest"),
        }
        for item in summaries
    ]
    return {
        "indicators": indicators_trimmed,
        "bar_time": str(last_bar.get("time") or ""),
        "last_close": last_bar.get("close"),
    }


def _trim_context_summary_multi(
    segments: list[dict[str, Any]],
) -> dict[str, Any]:
    """多时段上下文摘要：每时段一份裁剪后的指标快照。"""
    return {
        "timeframes": [str(seg.get("timeframe") or "") for seg in segments],
        "segments": [
            {
                "timeframe": seg.get("timeframe"),
                **_trim_context_summary(
                    list(seg.get("indicators") or []),
                    (seg.get("bars") or [{}])[-1],
                ),
            }
            for seg in segments
        ],
    }


def build_broadcast_payload(
    task: AIAnchorTask,
    broadcast: AIAnchorBroadcast,
    next_run_at: Any,
) -> dict[str, Any]:
    """WS 推送 payload：播报内容 + 任务进度信息。"""
    model_output = broadcast.model_output if isinstance(broadcast.model_output, dict) else {}
    advice = model_output.get("position_advice")
    return {
        "trading_mode": task.trading_mode,
        "task_id": str(task.id),
        "broadcast": {
            "id": str(broadcast.id),
            "symbol": broadcast.symbol,
            "timeframe": broadcast.timeframe,
            "direction": broadcast.direction,
            "entry": broadcast.entry,
            "take_profit": broadcast.take_profit,
            "stop_loss": broadcast.stop_loss,
            "action": broadcast.action,
            "commentary": broadcast.commentary,
            "model_error": broadcast.model_error,
            "key_levels": list(broadcast.key_levels or []),
            "position_advice": advice if isinstance(advice, dict) else None,
            "bar_time": broadcast.bar_time,
            "created_at": broadcast.created_at.isoformat() if broadcast.created_at else None,
        },
        "next_run_at": next_run_at.isoformat() if next_run_at else None,
    }


async def evaluate_anchor(
    session: AsyncSession,
    task: AIAnchorTask,
    redis_conn: Any,
) -> dict[str, Any]:
    """对单个到期任务执行一轮分析并播报（调用方负责 commit）。

    多时段任务：每时段各拉K线+算指标，一轮一次模型调用综合多周期分析；
    个别时段数据不足则跳过该时段，全部不足才降级观望播报。
    """
    now = utc_now()
    # 闭盘不播报：顺延一个间隔，开盘后自然恢复
    if not anchor_market_open(task):
        task.next_run_at = now + timedelta(minutes=task.interval_minutes)
        await session.flush()
        return {"skipped": "market_closed"}

    timeframes = task_timeframes(task)
    segments: list[dict[str, Any]] = []
    shortage: list[str] = []
    for tf in timeframes:
        bars = await get_kline_history(
            redis_conn,
            task.symbol,
            tf,
            limit=task.bar_count + WARMUP_BARS,
            end_time="",
            session=session,
            trading_mode=task.trading_mode,
        )
        if len(bars) < _MIN_BARS:
            shortage.append(f"{tf}仅{len(bars)}根")
            continue
        # 暖区全量算指标；模型只看最新 bar_count 根（最新一根为起点往历史）
        segments.append(
            {
                "timeframe": tf,
                "bars": bars[-task.bar_count :],
                "indicators": summarize_indicators(list(task.indicators or []), bars),
            }
        )

    if not segments:
        reason = f"K线数据不足（{';'.join(shortage) or '全部时段无数据'}），本轮播报降级为观望"
        parsed = _error_conclusion(reason)
        model_error = True
        primary_tf = timeframes[0]
        last_bar: dict[str, Any] = {}
        context_summary: dict[str, Any] = {"timeframes": timeframes, "segments": []}
    else:
        history = await build_context_history(session, task)
        primary_window = segments[0]["bars"]
        position_info = await _build_position_info(
            task,
            getattr(task, "manual_position", None),
            primary_window[-1].get("close") if primary_window else None,
            redis_conn,
        )
        messages = [
            {
                "role": "system",
                "content": build_system_prompt(
                    str(getattr(task, "direction_mode", None) or "any"),
                    has_position=position_info is not None,
                    strategy=str(getattr(task, "strategy", None) or "balanced"),
                    horizon=str(getattr(task, "horizon", None) or "short"),
                    timeframe_count=len(segments),
                ),
            },
            {
                "role": "user",
                "content": build_user_prompt(
                    task.symbol,
                    segments,
                    task.bar_count,
                    history,
                    position_info=position_info,
                ),
            },
        ]
        try:
            text = await chat_once(
                session,
                task.user_id,
                task.model_row_id,
                messages,
                _TEMPERATURE,
                _MAX_TOKENS,
            )
            parsed = parse_anchor_output(text)
            model_error = not parsed.get("parse_ok", True)
        except Exception as exc:  # 模型/网络异常：降级观望播报，不中断任务
            logger.warning("主播模型调用失败 task=%s err=%s", task.id, exc)
            parsed = _error_conclusion(f"模型调用失败：{exc}")
            model_error = True
        primary_tf = str(segments[0].get("timeframe") or task.timeframe)
        last_bar = segments[0]["bars"][-1] if segments[0]["bars"] else {}
        context_summary = _trim_context_summary_multi(segments)

    broadcast = AIAnchorBroadcast(
        task_id=task.id,
        user_id=task.user_id,
        symbol=task.symbol,
        timeframe=primary_tf,
        direction=str(parsed.get("direction") or "neutral"),
        entry=parsed.get("entry"),
        take_profit=parsed.get("take_profit"),
        stop_loss=parsed.get("stop_loss"),
        action=str(parsed.get("action") or "wait"),
        commentary=str(parsed.get("commentary") or ""),
        model_error=model_error,
        model_output={key: value for key, value in parsed.items() if key != "parse_ok"},
        key_levels=list(parsed.get("key_levels") or []),
        context_summary=context_summary,
        bar_time=str(last_bar.get("time") or "") or None,
    )
    session.add(broadcast)
    task.last_run_at = now
    task.next_run_at = now + timedelta(minutes=task.interval_minutes)
    await session.flush()

    # WS 推送（自包含 payload；推送失败不影响落库）
    try:
        from app.api.ws import broadcast_anchor

        broadcast_anchor(str(task.user_id), build_broadcast_payload(task, broadcast, task.next_run_at))
    except Exception:
        logger.exception("主播播报 WS 推送失败 task=%s", task.id)
    return {"broadcast": True, "model_error": model_error}
