"""TypeSafe Jev（System One）决策客户端 —— 量化交易 decision 策略

Jev 不是对话模型：POST /v1/systemone，一次调用 = 一份 state + 若干原子问题。
本客户端把交易上下文压成 state（K 线摘要 + 派生指标 + 持仓/预算），
用 Choice 原语问「下一步动作」，选项按当前持仓与多空标准动态构造：

- 无持仓：hold / open_long / open_short（按 side_mode 裁剪）
- 持多仓：hold / close_long / open_short（反向开仓先平后开由执行层处理）
- 持空仓：hold / close_short / open_long

返回与 LLM 决策同构的 {action, quantity, confidence, reason, parse_ok, raw}，
quantity 交 0 由 filter_ai_action 按仓位模式/预算重算；
confidence 直接用 Jev 的校准置信度（0~1，与 AI 决策口径一致）。

问题文本用英文（Jev 对英语准确度最佳，官方文档明确 CJK 不均）。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_model import AIModel
from app.models.ai_provider import AIProvider
from app.models.ai_trading.task import AITradingTask
from app.services.ai.providers.urls import resolve_endpoint
from app.services.crypto import decrypt

logger = logging.getLogger("qihuo.ai_trading")

# 决策模型客户端超时：Jev 延迟亚秒级，15s 足够宽裕
_JEV_TIMEOUT_S = 15.0

# state 里 K 线条数：默认跟 ai_bars_limit 同源，封顶 60（token 预算极小）
_MAX_BARS_IN_STATE = 60


def _bars_digest(bars: list[dict[str, Any]] | None, limit: int) -> list[list[Any]]:
    """K 线压成 [time, open, high, low, close, volume] 紧凑行"""
    out: list[list[Any]] = []
    if not isinstance(bars, list):
        return out
    for b in bars[-limit:]:
        try:
            out.append(
                [
                    str(b.get("time") or ""),
                    round(float(b.get("open") or 0), 3),
                    round(float(b.get("high") or 0), 3),
                    round(float(b.get("low") or 0), 3),
                    round(float(b.get("close") or 0), 3),
                    round(float(b.get("volume") or 0), 2),
                ]
            )
        except (TypeError, ValueError):
            continue
    return out


def _sma(closes: list[float], period: int) -> float | None:
    if len(closes) < period or period <= 0:
        return None
    return sum(closes[-period:]) / period


def _derived(bars: list[dict[str, Any]]) -> dict[str, Any]:
    """Python 侧算好派生指标放进 state：Jev 是判断模型，喂结论性特征比喂生数据可靠"""
    closes = [float(b.get("close") or 0) for b in bars if b.get("close")]
    if not closes:
        return {}
    last = closes[-1]
    ma5 = _sma(closes, 5)
    ma10 = _sma(closes, 10)
    ma20 = _sma(closes, 20)
    window = closes[-20:]
    return {
        "last_close": round(last, 3),
        "change_pct_5": (
            round((last / closes[-6] - 1) * 100, 3) if len(closes) >= 6 else None
        ),
        "change_pct_20": (
            round((last / closes[-21] - 1) * 100, 3) if len(closes) >= 21 else None
        ),
        "ma5": round(ma5, 3) if ma5 else None,
        "ma10": round(ma10, 3) if ma10 else None,
        "ma20": round(ma20, 3) if ma20 else None,
        "ma_alignment": (
            "bullish" if ma5 and ma10 and ma5 > ma10 > (ma20 or ma5)
            else "bearish" if ma5 and ma10 and ma5 < ma10 < (ma20 or ma5)
            else "mixed"
        ),
        "range20_high": round(max(window), 3) if window else None,
        "range20_low": round(min(window), 3) if window else None,
        "position_in_range20": (
            round((last - min(window)) / max(1e-9, max(window) - min(window)), 3)
            if window and max(window) > min(window)
            else None
        ),
    }


def build_state(task: AITradingTask, context: dict[str, Any]) -> dict[str, Any]:
    """引擎上下文 → Jev state（对象形式，字段有描述性名称）"""
    bars = list(context.get("recent_bars") or [])
    position = context.get("position") or None
    pos_digest = None
    if position:
        upnl = float(position.get("unrealized_pnl") or 0)
        margin = float(position.get("margin") or 0)
        pos_digest = {
            "direction": str(position.get("direction") or ""),
            "quantity": int(
                position.get("available_quantity")
                or position.get("quantity")
                or 0
            ),
            "avg_price": float(position.get("avg_price") or 0),
            "unrealized_pnl": upnl,
            # 相对保证金的浮盈亏%（负=浮亏）：模型自主止损的核心依据
            "unrealized_pnl_pct": (
                round(upnl / margin * 100, 2) if margin > 0 else None
            ),
        }
    return {
        "market": {
            "symbol": str(task.symbol or ""),
            "symbol_name": str(task.symbol_name or ""),
            "timeframe": str(task.timeframe or ""),
            "last_price": float(context.get("last_price") or 0),
        },
        "derived_indicators": _derived(bars),
        "recent_bars_ohlcv": _bars_digest(
            bars, min(_MAX_BARS_IN_STATE, int(context.get("ai_bars_limit") or 40))
        ),
        "position": pos_digest,
        "strategy_signal": _strategy_signal(context.get("strategy_signal")),
        "constraints": {
            "side_mode": str(task.side_mode or "both"),
            "hold_days_elapsed": context.get("hold_days"),
            "max_hold_days": context.get("max_hold_days"),
            "ai_budget_margin": context.get("ai_budget"),
            "qty_min": context.get("qty_min"),
            "qty_max": context.get("qty_max"),
        },
    }


def _strategy_signal(raw: Any) -> dict[str, Any] | None:
    """挂载的量化策略建议（混合模式：策略喂内容，模型做最终判断）"""
    if not isinstance(raw, dict) or not raw.get("action"):
        return None
    return {
        "kind": str(raw.get("kind") or ""),
        "suggested_action": str(raw.get("action") or "hold"),
        "reason": str(raw.get("reason") or "")[:160],
    }


def build_questions(state: dict[str, Any]) -> dict[str, Any]:
    """构造 Choice 动作问题：选项随持仓与多空标准变化"""
    constraints = state.get("constraints") or {}
    side_mode = str(constraints.get("side_mode") or "both")
    position = state.get("position") or None
    pos_dir = str((position or {}).get("direction") or "")

    criteria: dict[str, str] = {
        "hold": (
            "No action. Signals are mixed or the edge is insufficient; "
            "keep current exposure unchanged."
        ),
    }
    if not pos_dir or pos_dir == "flat":
        if side_mode in ("both", "long_only"):
            criteria["open_long"] = (
                "Open a new LONG position: the mounted strategy suggests "
                "going long, or trend indicators show clear upward momentum."
            )
        if side_mode in ("both", "short_only"):
            criteria["open_short"] = (
                "Open a new SHORT position: the mounted strategy suggests "
                "going short, or trend indicators show clear downward momentum."
            )
    else:
        if pos_dir == "long":
            criteria["close_long"] = (
                "Close the existing LONG position: uptrend is weakening or "
                "reversing, profit should be locked in, OR the position is "
                "losing and should be stopped out to cut further drawdown."
            )
            if side_mode in ("both", "short_only"):
                criteria["open_short"] = (
                    "Open a SHORT position (reversal against the current long): "
                    "strong downward signal; close the long first, then re-open "
                    "as short on a later decision."
                )
        elif pos_dir == "short":
            criteria["close_short"] = (
                "Close the existing SHORT position: downtrend is weakening or "
                "reversing, profit should be locked in, OR the position is "
                "losing and should be stopped out to cut further drawdown."
            )
            if side_mode in ("both", "long_only"):
                criteria["open_long"] = (
                    "Open a LONG position (reversal against the current short): "
                    "strong upward signal; close the short first, then re-open "
                    "as long on a later decision."
                )
        # 保持持仓观望的语义在 hold 里已覆盖

    return {
        "action": {
            "type": "choice",
            "instructions": {
                "question": (
                    "What trading action should be taken NOW for this futures "
                    "task? Judge from `derived_indicators`, `recent_bars_ohlcv` "
                    "trend, the current `position` and, when present, the "
                    "mounted `strategy_signal`. Respect `constraints.side_mode`."
                ),
                "notes": (
                    "Pick exactly one option. `strategy_signal` comes from a "
                    "mounted quant strategy with a validated entry edge — "
                    "treat it as a STRONG prior: when it suggests an opening "
                    "action, FOLLOW it unless price action clearly "
                    "contradicts it (strong opposite momentum or a fresh "
                    "opposing breakout). You may override it only with "
                    "concrete contradicting evidence, not vague caution. "
                    "When the signal is hold or absent, open only on your "
                    "own strong conviction. Do not demand extra "
                    "confirmations beyond the signal — hesitation costs "
                    "the entry. Closing is preferred when the move that "
                    "justified the position has stalled or reversed. When "
                    "holding, weigh `position.unrealized_pnl_pct`: a deep "
                    "negative drawdown usually deserves a stop-loss close "
                    "unless a highly probable reversal is underway."
                ),
            },
            "criteria": criteria,
        }
    }


def parse_action_answer(
    answers: dict[str, Any],
    has_position: bool = False,
) -> dict[str, Any]:
    """Jev answers → 引擎决策 dict（与 LLM 决策同构）

    has_position：当前是否有持仓。Jev 的 hold 语义是"keep current
    exposure unchanged"——有仓时中文标签应为「持仓保持」而非「观望」。
    """
    ans = (answers or {}).get("action") or {}
    choice = str(ans.get("choice") or "hold").strip().lower()
    try:
        conf = float(ans.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    probs = ans.get("probabilities") if isinstance(ans.get("probabilities"), dict) else {}

    mapping = {
        "hold": "hold",
        "open_long": "open_long",
        "open_short": "open_short",
        "close_long": "close",
        "close_short": "close",
    }
    action = mapping.get(choice, "hold")
    label = {
        "open_long": "开多",
        "open_short": "开空",
        "close": "平仓",
        "hold": "持仓保持" if has_position else "观望",
    }.get(action, action)
    top2 = sorted(
        ((k, v) for k, v in probs.items() if isinstance(v, (int, float))),
        key=lambda kv: -float(kv[1]),
    )[:2]
    prob_txt = " ".join(f"{k}:{float(v):.2f}" for k, v in top2)
    reason = f"Jev {choice}→{label} 置信{conf:.2f}" + (f" | {prob_txt}" if prob_txt else "")
    return {
        "action": action,
        "quantity": 0,  # 交 filter_ai_action 按仓位模式/预算重算
        "confidence": max(0.0, min(1.0, conf)),
        "reason": reason,
        "parse_ok": True,
        "raw": {"choice": choice, "confidence": conf, "probabilities": probs},
        "jev_model": (answers or {}).get("model"),
    }


async def call_jev_decision(
    session: AsyncSession,
    task: AITradingTask,
    context: dict[str, Any],
) -> dict[str, Any]:
    """决策模型一跳：加载 Jev 渠道 → POST /v1/systemone → 解析动作"""
    stmt = (
        select(AIModel, AIProvider)
        .join(AIProvider, AIModel.provider_id == AIProvider.id)
        .where(
            AIModel.id == task.model_row_id,
            AIModel.user_id == task.user_id,
        )
    )
    result = await session.execute(stmt)
    row = result.first()
    if row is None:
        return {
            "action": "hold",
            "quantity": 0,
            "reason": "决策模型不存在",
            "confidence": 0,
            "raw": None,
            "parse_ok": False,
        }
    model, provider = row
    if str(provider.api_type or "") != "jev":
        return {
            "action": "hold",
            "quantity": 0,
            "reason": "绑定的模型不是 Jev 决策模型",
            "confidence": 0,
            "raw": None,
            "parse_ok": False,
        }
    api_key = decrypt(provider.api_key)
    state = build_state(task, context)
    questions = build_questions(state)
    url = resolve_endpoint(str(provider.base_url or ""), "/systemone")
    payload = {
        "state": state,
        "model": str(model.model_id or "jev-latest"),
        "questions": questions,
    }

    try:
        async with httpx.AsyncClient(timeout=_JEV_TIMEOUT_S) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except httpx.TimeoutException:
        logger.warning("决策模型超时 task=%s url=%s", task.id, url)
        return {
            "action": "hold",
            "quantity": 0,
            "reason": "决策模型超时，本轮观望",
            "confidence": 0,
            "raw": None,
            "parse_ok": False,
        }
    except httpx.HTTPError as exc:
        logger.warning("决策模型网络错误 task=%s err=%s", task.id, exc)
        return {
            "action": "hold",
            "quantity": 0,
            "reason": f"决策模型网络错误: {type(exc).__name__}",
            "confidence": 0,
            "raw": None,
            "parse_ok": False,
        }

    if resp.status_code != 200:
        logger.warning(
            "决策模型返回异常 task=%s http=%s body=%s",
            task.id,
            resp.status_code,
            resp.text[:200],
        )
        return {
            "action": "hold",
            "quantity": 0,
            "reason": f"决策模型 HTTP {resp.status_code}",
            "confidence": 0,
            "raw": {"http_status": resp.status_code, "body": resp.text[:500]},
            "parse_ok": False,
        }

    try:
        body = resp.json()
    except ValueError:
        return {
            "action": "hold",
            "quantity": 0,
            "reason": "决策模型响应非 JSON",
            "confidence": 0,
            "raw": None,
            "parse_ok": False,
        }
    answers = body.get("answers") if isinstance(body.get("answers"), dict) else {}
    has_pos = bool(
        (context.get("position") or {}).get("direction") not in (None, "", "flat")
    )
    out = parse_action_answer(answers, has_position=has_pos)
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    if usage:
        out["raw"]["usage"] = usage
    return out
