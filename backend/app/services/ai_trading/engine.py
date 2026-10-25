"""AI 交易评估引擎 —— 规则优先 + 模型决策 + paper 下单"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_trading.decision import AITradingDecision
from app.models.ai_trading.task import AITradingTask
from app.core.trading_mode import normalize_trading_mode
from app.services.ai_trading.core.bar_align import next_bar_boundary, should_run_for_bar
from app.services.ai_trading.core.capital import (
    ai_available_budget,
    apply_style_to_trade_rules,
    check_max_hold,
    hold_days_elapsed,
    horizon_label,
)
from app.services.ai_trading.core.execute import execute_action, find_symbol_position
from app.services.ai_trading.core.risk import check_hard_rules, filter_ai_action
from app.services.ai_trading.decision_text import public_decision_reason
from app.services.ai_trading.equity import cash_delta_pct, snapshot_equity
from app.services.ai_trading.llm.model_client import call_model_decision
from app.services.ai_trading.task.position_pnl import source_for_task as _source_for_task
from app.services.kline import get_kline_history
from app.services.paper.account_svc import get_account_summary
from app.services.paper.price_band import resolve_last_price
from app.services.paper.specs import resolve_trade_params
from app.services.trading_hours import get_session_status, is_near_day_close

from app.services.ai_trading.task.run_log_svc import write_run_log

logger = logging.getLogger("qihuo.ai_trading")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def _write_decision(
    session: AsyncSession,
    task: AITradingTask,
    *,
    bar_time: str | None,
    trigger_type: str,
    context: dict[str, Any] | None,
    model_output: dict[str, Any] | None,
    action: str,
    order_id: UUID | None,
    reason: str,
) -> AITradingDecision:
    # 日志瘦身：多周期 K 线可达数百根，入库只留各周期根数摘要（前端不展示原始 bars）
    summary = context
    if isinstance(context, dict):
        summary = dict(context)
        rb = summary.get("recent_bars")
        if isinstance(rb, list):
            summary["recent_bars"] = {"count": len(rb)}
        eb = summary.get("extra_bars")
        if isinstance(eb, dict):
            summary["extra_bars"] = {
                tf: {"count": len(v)}
                for tf, v in eb.items()
                if isinstance(v, list)
            }
    row = AITradingDecision(
        task_id=task.id,
        user_id=task.user_id,
        bar_time=bar_time,
        timeframe=task.timeframe,
        trigger_type=trigger_type,
        context_summary=summary,
        model_output=model_output,
        action=action,
        order_id=order_id,
        reason=reason[:2000],
        created_at=_now_utc(),
    )
    session.add(row)
    await session.flush()
    return row


async def _apply_win_stats(
    session: AsyncSession,
    task: AITradingTask,
    order_id: UUID | None,
) -> None:
    """平仓成功后按 realized_pnl 正负增量更新任务胜率统计。

    realized_pnl > 0 记一笔盈利，否则记一笔亏损；每次平仓 trade_count+1，
    并重算 win_rate = win_count / trade_count * 100。
    """
    if not order_id:
        return
    from app.models.paper.order import PaperOrder
    from sqlalchemy import select

    row = (
        await session.execute(
            select(PaperOrder.realized_pnl).where(PaperOrder.id == order_id)
        )
    ).scalar_one_or_none()
    if row is None:
        return
    realized = Decimal(str(row or 0))
    win = int(getattr(task, "win_count", 0) or 0)
    loss = int(getattr(task, "loss_count", 0) or 0)
    if realized > 0:
        win += 1
    else:
        loss += 1
    total = win + loss
    task.win_count = win
    task.loss_count = loss
    task.trade_count = total
    task.win_rate = Decimal(str(round(win / total * 100, 2))) if total > 0 else Decimal("0")


async def force_close_symbol(
    session: AsyncSession,
    task: AITradingTask,
    redis_conn: object | None,
    trigger_type: str,
) -> None:
    """强制平掉任务品种持仓"""
    mode = normalize_trading_mode(getattr(task, "trading_mode", None))
    position = await find_symbol_position(
        session,
        task.user_id,
        task.symbol,
        mode,
        _source_for_task(task),
        task_id=task.id,
    )
    if not position:
        await _write_decision(
            session,
            task,
            bar_time=None,
            trigger_type=trigger_type,
            context=None,
            model_output=None,
            action="hold",
            order_id=None,
            reason="结束任务时无持仓",
        )
        return
    last_ref = await resolve_last_price(task.symbol, redis_conn, None, trading_mode=mode, allow_mock=False)
    price = float(last_ref or position.get("avg_price") or 0)
    qty = int(position.get("available_quantity") or position.get("quantity") or 0)
    order_id, msg = await execute_action(
        session, task, "close", qty, price, redis_conn
    )
    if order_id:
        await _apply_win_stats(session, task, order_id)
    await _write_decision(
        session,
        task,
        bar_time=None,
        trigger_type=trigger_type,
        context={"position": position, "last_price": price},
        model_output=None,
        action="close" if order_id else "hold",
        order_id=order_id,
        reason=msg,
    )


def _check_exit_standards(
    task: AITradingTask,
    strategy_type: str,
    signal_bars: list[dict[str, Any]],
    position: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """平仓标准扩展检查（close_rules 用户原值，不参与风格缩放）

    - 技术指标：持多仓遇死叉平多、持空仓遇金叉平空，每根新收盘 K 线检查
    - 因子阈值：量化 factor 任务用自身公式；AI 任务用挂载的参考因子
      （旧编码因子宁可不触发平仓标准，也不能用错值触发）
    """
    from app.services.ai_trading.core.exit_signals import (
        check_indicator_exit_rules,
        evaluate_factor_exit_rules,
    )

    close_rules = task.close_rules or {}
    hit = check_indicator_exit_rules(
        close_rules.get("indicator_exits"), signal_bars, position
    )
    if hit:
        return hit

    factor_exit = close_rules.get("factor_exit")
    if not factor_exit:
        return None
    sp = getattr(task, "strategy_params", None) or {}
    tokens = sp.get("factor_tokens") or []
    if not tokens:
        return None
    from app.services.factor_lab.token_encoding import looks_like_v1
    from app.services.factor_lab.vm import FEAT_COUNT

    is_combo = isinstance(tokens[0], (list, tuple))
    flat_groups = (
        [list(g) for g in tokens if isinstance(g, (list, tuple))]
        if is_combo
        else [list(tokens)]
    )
    if any(looks_like_v1(g, FEAT_COUNT) for g in flat_groups if g):
        return None

    from app.services.ai_trading.strategies.factor import (
        _latest_position,
        normalize_factor_params,
    )

    p = normalize_factor_params(sp)
    pos = _latest_position(signal_bars, p["factor_tokens"], p.get("factor_weights"))
    if pos is None:
        return None
    hit = evaluate_factor_exit_rules(factor_exit, pos, position)
    if hit:
        hit.setdefault("context", {}).update(
            {
                "factor_position": round(float(pos), 3),
                "factor_text": p.get("factor_text") or "",
            }
        )
    return hit


def _quant_fast_interval(task: AITradingTask) -> int:
    """量化任务快频评估间隔（秒）：0=未启用（按 K 线收盘对齐）

    全部量化策略生效；decision 任务走 decision_interval_sec，不消费本字段。
    设定时按任务周期二次钳制到 [60, 周期秒数]，防创建后又改小周期越界。
    """
    raw = int(getattr(task, "quant_interval_sec", 0) or 0)
    if raw <= 0:
        return 0
    stype = str(getattr(task, "strategy_type", "") or "").strip().lower()
    from app.services.ai_trading.strategies import QUANT_STRATEGY_TYPES

    if stype not in QUANT_STRATEGY_TYPES:
        return 0
    from app.services.ai_trading.core.bar_align import timeframe_seconds

    tf_sec = timeframe_seconds(str(getattr(task, "timeframe", "") or ""))
    if tf_sec <= 0:
        return 0
    return max(60, min(tf_sec, raw))


async def evaluate_task(
    session: AsyncSession,
    task: AITradingTask,
    redis_conn: object | None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """评估单个 running 任务；force=True 忽略 bar 对齐"""
    if task.status != "running" and not force:
        return {"skipped": True, "reason": "not_running"}

    mode = normalize_trading_mode(getattr(task, "trading_mode", None))
    session_status = get_session_status(task.symbol)
    market_open = bool(session_status.get("is_open"))
    # session_close 平仓：日盘收盘前 5 分钟触发（市场仍开 → 能下单）；
    # 不在已收盘时触发（此时下单必被交易时段校验拒绝，导致永远平不掉）。
    session_closing = (
        bool((task.close_rules or {}).get("session_close"))
        and market_open
        and is_near_day_close(5)
    )

    bars = await get_kline_history(
        redis_conn,
        task.symbol,
        task.timeframe,
        # 240 根：因子特征层 zscore 窗口 200 + 时序窗口，80 根会让归一化
        # 口径与挖掘/回测严重偏离（窗口填不满、因子值漂移）
        limit=240,
        end_time="",
        session=session,
        trading_mode=mode,
    )
    strategy_type = str(getattr(task, "strategy_type", None) or "ai").strip().lower()
    is_decision = strategy_type == "decision"
    # 量化快频间隔（>0 时按间隔评估，不按 K 线收盘对齐；decision 不消费）
    quant_interval = _quant_fast_interval(task)
    run_bar = True
    closed_bar: dict[str, Any] | None = None
    if not force:
        if is_decision:
            # 决策模型：按设定间隔评估（60-300s，Jev 低延迟适合分钟级驱动），
            # 不按 K 线收盘对齐；closed_bar 取最后一根已收盘 K 仅作记录。
            # 止损/收盘等硬规则仍每 tick 检查（硬规则块在 run_bar 门之前）
            closed_bar = bars[-2] if len(bars) >= 2 else (bars[-1] if bars else None)
            interval = max(
                60, min(300, int(getattr(task, "decision_interval_sec", 0) or 60))
            )
            last_run = getattr(task, "last_run_at", None)
            elapsed = (
                (_now_utc() - last_run).total_seconds() if last_run else None
            )
            run_bar = elapsed is None or elapsed >= interval
        elif quant_interval > 0:
            # 量化快频：按设定间隔评估（60s ~ K 线周期），不按收盘对齐。
            # closed_bar 取 bars[-1]（含未收盘 bar）→ 下方 signal_bars 截断
            # 到此，策略信号计算序列带上实时未收盘 bar，盘中即可捕捉信号变化；
            # 决策记录的 bar_time 即该未收盘 bar 的时间。
            closed_bar = bars[-1] if bars else None
            last_run = getattr(task, "last_run_at", None)
            elapsed = (
                (_now_utc() - last_run).total_seconds() if last_run else None
            )
            run_bar = elapsed is None or elapsed >= quant_interval
        else:
            run_bar, closed_bar = should_run_for_bar(bars, task.last_bar_time)
    else:
        closed_bar = bars[-2] if len(bars) >= 2 else (bars[-1] if bars else None)
        run_bar = closed_bar is not None

    # 交易定价禁用 mock 兜底：无报价时回落已收盘 bar 收盘价
    last_ref = await resolve_last_price(task.symbol, redis_conn, None, trading_mode=mode, allow_mock=False)
    last_price = float(last_ref or 0)
    if last_price <= 0 and closed_bar:
        last_price = float(closed_bar.get("close") or 0)

    position = await find_symbol_position(
        session,
        task.user_id,
        task.symbol,
        mode,
        _source_for_task(task),
        task_id=task.id,
    )
    summary = await get_account_summary(session, task.user_id, mode)
    equity = float(summary.get("total_equity") or 0)
    delta_pct = cash_delta_pct(task, equity)
    # 同步持仓开仓时间：有仓无时间则补记；无仓则清空
    if position and getattr(task, "position_opened_at", None) is None:
        task.position_opened_at = _now_utc()
    if not position:
        task.position_opened_at = None

    # 策略风格：在用户止盈/止损/持仓上限范围内缩放生效规则
    # 启用用户提示词时风格失效，规则按用户原值执行
    risk_style = str(getattr(task, "risk_style", None) or "balanced")
    custom_prompt_on = bool(getattr(task, "custom_prompt_enabled", False))
    styled = apply_style_to_trade_rules(
        risk_style=risk_style,
        close_rules=task.close_rules or {},
        stop_rules=task.stop_rules or {},
        max_hold_days=int(getattr(task, "max_hold_days", None) or 10),
        custom_prompt_enabled=custom_prompt_on,
    )
    eff_close = styled["close_rules"]
    eff_stop = styled["stop_rules"]
    eff_max_hold = int(styled["max_hold_days"])
    style_active = bool(styled.get("style_active", not custom_prompt_on))
    # 硬规则 reason 标签：风格生效用 risk_style；用户提示词模式用 custom_prompt
    hard_style_tag = risk_style if style_active else "custom_prompt"

    hard = check_hard_rules(
        position=position,
        last_price=last_price,
        stop_rules=eff_stop,
        close_rules=eff_close,
        cash_delta_pct=delta_pct,
        session_closing=session_closing,
        risk_style=hard_style_tag,
    )
    # 最长持仓硬约束（风格缩放后的上限，优先于 AI）
    if hard is None:
        hard = check_max_hold(
            position=position,
            position_opened_at=getattr(task, "position_opened_at", None),
            max_hold_days=eff_max_hold,
            now=_now_utc(),
        )

    if hard and (session_closing or market_open or hard.get("trigger_type") == "max_hold"):
        order_id, msg = await execute_action(
            session,
            task,
            hard["action"],
            int(hard.get("quantity") or 0),
            last_price,
            redis_conn,
        )
        if hard["action"] == "close" and order_id:
            task.position_opened_at = None
            await _apply_win_stats(session, task, order_id)
        await _write_decision(
            session,
            task,
            bar_time=str((closed_bar or {}).get("time") or "") or None,
            trigger_type=str(hard["trigger_type"]),
            context={"last_price": last_price, "position": position, "equity": equity},
            model_output=None,
            action=hard["action"],
            order_id=order_id,
            reason=f"{hard['reason']}；{msg}",
        )
        await write_run_log(
            session, task,
            level="warn",
            event="hard_rule" if hard.get("trigger_type") != "session_close" else "session_close",
            action=hard["action"],
            order_id=order_id,
            reason=f"{hard['reason']}；{msg}",
            detail={
                "trigger_type": hard.get("trigger_type"),
                "last_price": last_price,
                "position": position,
                "equity": equity,
            },
            bar_time=str((closed_bar or {}).get("time") or "") or None,
        )
        task.last_run_at = _now_utc()
        task.updated_at = task.last_run_at
        task.note = None  # 硬规则正常触发，清过期异常 note
        if closed_bar and run_bar:
            task.last_bar_time = str(closed_bar.get("time") or task.last_bar_time)
        await session.flush()
        await snapshot_equity(session, task, redis_conn)
        return {
            "action": hard["action"],
            "order_id": str(order_id) if order_id else None,
        }

    if not run_bar:
        if is_decision:
            # 决策模型未到评估间隔：不动 last_run_at（间隔锚点），不写决策记录
            return {"skipped": True, "reason": "decision_interval_wait"}
        if quant_interval > 0:
            # 量化快频未到间隔：同决策模型，不动 last_run_at（间隔锚点）
            return {"skipped": True, "reason": "quant_interval_wait"}
        # 无新 bar：评估本身健康（能走到这里说明没抛异常），清掉过期的「评估异常」note，
        # 避免修复崩溃后日线/低频任务因长期 skip 而 note 一直挂着误导用户
        task.last_run_at = _now_utc()
        task.note = None
        await session.flush()
        return {"skipped": True, "reason": "no_new_bar"}

    if not market_open:
        task.last_run_at = _now_utc()
        if closed_bar:
            task.last_bar_time = str(closed_bar.get("time") or "")
        task.next_bar_ts = next_bar_boundary(task.last_run_at, task.timeframe)
        task.updated_at = task.last_run_at
        task.note = None  # 休市 skip 也是健康评估，清过期异常 note
        await session.flush()
        return {"skipped": True, "reason": "market_closed"}

    # AI 决策每周期查看根数（默认 40，10-240；量化信号计算不受影响仍用 240 根）
    ai_bars_limit = max(10, min(240, int(getattr(task, "ai_bars_limit", 0) or 40)))
    recent = bars[-ai_bars_limit:] if bars else []
    # 均线策略：用到「当前已收盘 bar」为止的序列（force 时 closed_bar 可能是 bars[-1]）
    # 因子策略特征层 zscore 窗口 200，需保留完整 240 根（与 get_kline_history limit 对齐）
    signal_bars = bars[-240:] if bars else []
    if closed_bar is not None and bars:
        closed_time = str(closed_bar.get("time") or "")
        if closed_time:
            cut = None
            for idx, b in enumerate(bars):
                if str(b.get("time") or "") == closed_time:
                    cut = idx
            if cut is not None:
                signal_bars = bars[: cut + 1]
                if len(signal_bars) > 240:
                    signal_bars = signal_bars[-240:]
    # 扩展信息源(桌面端挖掘的 sec 组 token):bars 挂上板块动量 x_* 字段后
    # 特征矩阵才出扩展行,含这类 token 的因子才有信号;不需要时原样返回
    try:
        from app.services.factor_lab.aux_attach import (
            attach_aux_for_tokens,
            collect_factor_tokens,
            tokens_need_attach,
        )

        _aux_tokens = collect_factor_tokens(task.strategy_params)
        if tokens_need_attach(_aux_tokens):
            signal_bars = await attach_aux_for_tokens(
                session, task.symbol, task.timeframe, signal_bars, _aux_tokens
            )
    except Exception:
        logger.warning("aux attach 失败:%s", task.symbol, exc_info=True)
    params = resolve_trade_params(task.symbol, None)

    # 平仓标准扩展：技术指标死叉/金叉 + 因子阈值（close_rules 用户原值，不参与风格缩放）。
    # 走到这里已通过 run_bar / market_open 门 → 每根新收盘 K 线评估一次；
    # 命中先于策略信号 / AI 决策强制平仓（优先级在止损/盈亏%硬规则之后）
    exit_hit = _check_exit_standards(task, strategy_type, signal_bars, position)
    if exit_hit:
        order_id, msg = await execute_action(
            session,
            task,
            exit_hit["action"],
            int(exit_hit.get("quantity") or 0),
            last_price,
            redis_conn,
        )
        if exit_hit["action"] == "close" and order_id:
            task.position_opened_at = None
            await _apply_win_stats(session, task, order_id)
        await _write_decision(
            session,
            task,
            bar_time=str((closed_bar or {}).get("time") or "") or None,
            trigger_type=str(exit_hit["trigger_type"]),
            context={
                "last_price": last_price,
                "position": position,
                "equity": equity,
                **(exit_hit.get("context") or {}),
            },
            model_output=None,
            action=exit_hit["action"],
            order_id=order_id,
            reason=f"{exit_hit['reason']}；{msg}",
        )
        await write_run_log(
            session, task,
            level="warn",
            event="exit_rule",
            action=exit_hit["action"],
            order_id=order_id,
            reason=f"{exit_hit['reason']}；{msg}",
            detail={
                "trigger_type": exit_hit.get("trigger_type"),
                **(exit_hit.get("context") or {}),
                "last_price": last_price,
                "position": position,
                "equity": equity,
            },
            bar_time=str((closed_bar or {}).get("time") or "") or None,
        )
        task.last_run_at = _now_utc()
        task.updated_at = task.last_run_at
        task.note = None  # 出场标准正常触发，清过期异常 note
        if closed_bar and run_bar:
            task.last_bar_time = str(closed_bar.get("time") or task.last_bar_time)
        await session.flush()
        await snapshot_equity(session, task, redis_conn)
        return {
            "action": exit_hit["action"],
            "order_id": str(order_id) if order_id else None,
        }

    from app.services.ai_trading.strategies import (
        QUANT_STRATEGY_TYPES,
        compute_quant_signal,
    )

    context: dict[str, Any] | None = None  # AI 分支填充完整上下文；量化路径仅日志快照用
    if strategy_type in QUANT_STRATEGY_TYPES:
        model_out = compute_quant_signal(
            strategy_type,
            signal_bars,
            getattr(task, "strategy_params", None) or {},
            task.side_mode,
            position,
            exit_rules=(task.close_rules or {}).get("factor_exit"),
        )
        close_ai = True
        stop_ai = True
        trigger = strategy_type
    elif is_decision:
        # 决策模型（TypeSafe Jev System One）：做多/做空/平仓全由模型判断；
        # 手数与风控仍走 filter_ai_action 安全链，止损等硬规则在上方每 tick 生效
        from app.services.ai_trading.llm.decision_client import call_jev_decision

        pos_margin = float((position or {}).get("margin") or 0)
        allocated = float(getattr(task, "allocated_capital", 0) or 0)
        usage_min = float(getattr(task, "capital_usage_min_pct", None) or 0)
        usage_max = float(getattr(task, "capital_usage_max_pct", None) or 100)
        qty_min = int(getattr(task, "qty_min", None) or task.fixed_qty or 1)
        qty_max = int(getattr(task, "qty_max", None) or qty_min)
        if qty_max < qty_min:
            qty_min, qty_max = qty_max, qty_min
        vault_free = max(0.0, allocated - pos_margin)
        ai_budget = ai_available_budget(
            allocated_capital=allocated,
            risk_style=risk_style,
            account_available=vault_free,
            position_margin=pos_margin,
            custom_prompt_enabled=False,
            capital_usage_max_pct=usage_max,
            capital_usage_min_pct=usage_min,
        )
        hold_days = hold_days_elapsed(
            getattr(task, "position_opened_at", None), _now_utc()
        )
        # 混合模式：挂载的量化策略算出信号，作为决策模型的参考输入
        # （策略给模型喂内容，模型做最终投资判断，可推翻策略建议）
        ds_cfg = (getattr(task, "strategy_params", None) or {}).get(
            "decision_strategy"
        )
        strategy_signal: dict[str, Any] | None = None
        if (
            isinstance(ds_cfg, dict)
            and str(ds_cfg.get("kind") or "") in QUANT_STRATEGY_TYPES
        ):
            try:
                sig = compute_quant_signal(
                    str(ds_cfg.get("kind")),
                    signal_bars,
                    ds_cfg.get("params") or {},
                    task.side_mode,
                    position,
                )
                strategy_signal = {
                    "kind": str(ds_cfg.get("kind")),
                    "action": str(sig.get("action") or "hold"),
                    "reason": str(sig.get("reason") or "")[:160],
                    "confidence": sig.get("confidence"),
                }
            except Exception:
                logger.warning(
                    "决策任务策略信号计算失败 task=%s", task.id, exc_info=True
                )
        context = {
            "symbol": task.symbol,
            "symbol_name": task.symbol_name,
            "timeframe": task.timeframe,
            "side_mode": task.side_mode,
            "qty_min": qty_min,
            "qty_max": qty_max,
            "risk_style": risk_style,
            "max_hold_days": eff_max_hold,
            "hold_days": hold_days,
            "allocated_capital": allocated,
            "ai_budget": ai_budget,
            "last_price": last_price,
            "position": position,
            "recent_bars": recent,
            "ai_bars_limit": ai_bars_limit,
            "strategy_signal": strategy_signal,
        }
        try:
            model_out = await call_jev_decision(session, task, context)
        except Exception as exc:
            logger.exception("决策模型调用失败 task=%s", task.id)
            model_out = {
                "action": "hold",
                "quantity": 0,
                "reason": f"决策模型异常: {exc}",
                "confidence": 0,
                "raw": None,
                "parse_ok": False,
            }
        # 模型自决平仓：close_rules.ai_auto（创建时由「模型自己决定平仓」写入）
        close_ai = bool((task.close_rules or {}).get("ai_auto", True))
        stop_ai = bool((task.stop_rules or {}).get("ai_auto", True))
        trigger = "decision"
    else:
        pos_margin = float((position or {}).get("margin") or 0)
        allocated = float(getattr(task, "allocated_capital", 0) or 0)
        # risk_style / eff_* 已在上方风格缩放阶段计算
        usage_min = float(getattr(task, "capital_usage_min_pct", None) or 0)
        usage_max = float(getattr(task, "capital_usage_max_pct", None) or 100)
        qty_min = int(getattr(task, "qty_min", None) or task.fixed_qty or 1)
        qty_max = int(getattr(task, "qty_max", None) or qty_min)
        if qty_max < qty_min:
            qty_min, qty_max = qty_max, qty_min
        # AI 预算只看资金仓剩余，不看主账户 available
        vault_free = max(0.0, allocated - pos_margin)
        ai_budget = ai_available_budget(
            allocated_capital=allocated,
            risk_style=risk_style,
            account_available=vault_free,
            position_margin=pos_margin,
            custom_prompt_enabled=custom_prompt_on,
            capital_usage_max_pct=usage_max,
            capital_usage_min_pct=usage_min,
        )
        hold_days = hold_days_elapsed(
            getattr(task, "position_opened_at", None), _now_utc()
        )
        style_profile = styled.get("profile") or {}
        # P4：AI 任务可选注入因子信号（strategy_params.factor_tokens）
        _ai_factor_tokens = list(
            (getattr(task, "strategy_params", None) or {}).get("factor_tokens")
            or []
        )
        ai_factor_signal = None
        if _ai_factor_tokens:
            from app.services.factor_lab.token_encoding import looks_like_v1
            from app.services.factor_lab.vm import FEAT_COUNT

            # 组合形态（list of lists）：逐组做旧编码检查，任一命中即跳过
            _is_combo = isinstance(_ai_factor_tokens[0], (list, tuple))
            _flat_groups = (
                [list(g) for g in _ai_factor_tokens if isinstance(g, (list, tuple))]
                if _is_combo
                else [_ai_factor_tokens]
            )
            _legacy = any(looks_like_v1(g, FEAT_COUNT) for g in _flat_groups if g)
            if _legacy:
                # 旧编码会解码成别的算子，宁可不注入信号也不能给错信号
                logger.warning(
                    "任务 %s 的 factor_tokens 是旧版编码，已跳过因子信号；"
                    "请运行 scripts/migrate-factor-token-encoding.py 迁移",
                    getattr(task, "id", "?"),
                )
            else:
                from app.services.ai_trading.strategies import factor_snapshot

                _w = (
                    (getattr(task, "strategy_params", None) or {}).get(
                        "factor_weights"
                    )
                    if _is_combo
                    else None
                )
                ai_factor_signal = factor_snapshot(
                    _ai_factor_tokens, signal_bars, weights=_w
                )
        # P4b：AI 任务可选注入多选量化策略参考信号（strategy_params.ref_strategies）
        from app.services.ai_trading.strategies.quant_ref import (
            build_quant_ref_signals,
        )

        _ai_ref_strategies = list(
            (getattr(task, "strategy_params", None) or {}).get("ref_strategies")
            or []
        )
        ai_quant_ref_signals = (
            build_quant_ref_signals(_ai_ref_strategies, signal_bars) or None
        )
        # 多周期共振：附加周期各拉 ai_bars_limit 根；单周期失败不阻断主流程
        extra_tf_bars: dict[str, list[dict[str, Any]]] = {}
        for tf in [
            x
            for x in (getattr(task, "extra_timeframes", None) or [])
            if x and x != task.timeframe
        ][:3]:
            try:
                tf_bars = await get_kline_history(
                    redis_conn,
                    task.symbol,
                    tf,
                    limit=ai_bars_limit,
                    end_time="",
                    session=session,
                    trading_mode=mode,
                )
                if tf_bars:
                    extra_tf_bars[tf] = tf_bars[-ai_bars_limit:]
            except Exception:
                logger.warning(
                    "多周期共振拉取失败 task=%s tf=%s", task.id, tf, exc_info=True
                )
        context = {
            "symbol": task.symbol,
            "symbol_name": task.symbol_name,
            "timeframe": task.timeframe,
            "horizon": horizon_label(str(task.timeframe)),
            "side_mode": task.side_mode,
            "position_mode": task.position_mode,
            "fixed_qty": task.fixed_qty,
            "qty_min": qty_min,
            "qty_max": qty_max,
            "capital_usage_min_pct": usage_min,
            "capital_usage_max_pct": usage_max,
            "risk_style": risk_style,
            "style_active": style_active,
            "custom_prompt_enabled": custom_prompt_on,
            "style_pace": style_profile.get("pace"),
            "style_hint": style_profile.get("prompt_hint"),
            # 用户基准 vs 风格生效
            "user_max_hold_days": int(styled.get("user_max_hold_days") or eff_max_hold),
            "max_hold_days": eff_max_hold,
            "hold_days": hold_days,
            "allocated_capital": allocated,
            "ai_budget": ai_budget,
            "last_price": last_price,
            "position": position,
            "account": {
                "total_equity": equity,
                "available_margin": summary.get("available_margin"),
                "cash_delta_pct": delta_pct,
            },
            # 给模型看生效规则，并附用户原值便于理解风格缩放
            "user_close_rules": task.close_rules,
            "user_stop_rules": task.stop_rules,
            "close_rules": {
                k: v
                for k, v in eff_close.items()
                if not str(k).startswith("_")
            },
            "stop_rules": {
                k: v
                for k, v in eff_stop.items()
                if not str(k).startswith("_")
            },
            "recent_bars": recent,
            "ai_bars_limit": ai_bars_limit,
            "extra_bars": extra_tf_bars or None,
            "factor_signal": ai_factor_signal,
            "quant_ref_signals": ai_quant_ref_signals,
            "closed_bar": closed_bar,
            "goal": (
                "多赚少亏，以完成盈利目标为第一优先级；"
                + (
                    "已启用用户提示词，策略风格失效，按用户提示词执行"
                    if custom_prompt_on
                    else "风格在用户规则范围内调节节奏"
                )
            ),
        }
        try:
            model_out = await call_model_decision(session, task, context)
        except Exception as exc:
            logger.exception("模型调用失败 task=%s", task.id)
            model_out = {
                "action": "hold",
                "quantity": 0,
                "reason": f"模型异常: {exc}",
                "confidence": 0,
                "raw": None,
            }
        close_ai = bool((task.close_rules or {}).get("ai_auto", True))
        stop_ai = bool((task.stop_rules or {}).get("ai_auto", True))
        trigger = "bar"

    mult = int(params.get("multiplier") or 10)
    rate = float(params.get("margin_rate") or 0.1)
    usage_min = float(getattr(task, "capital_usage_min_pct", None) or 0)
    usage_max = float(getattr(task, "capital_usage_max_pct", None) or 100)
    qty_min = int(getattr(task, "qty_min", None) or task.fixed_qty or 1)
    qty_max = int(getattr(task, "qty_max", None) or qty_min)
    alloc = float(getattr(task, "allocated_capital", 0) or 0)
    p_margin = float((position or {}).get("margin") or 0)
    if alloc > 0:
        # 有资金仓 → per-task 预算（AI/量化/因子统一）
        vault_free = max(0.0, alloc - p_margin)
        budget_margin = float(
            ai_available_budget(
                allocated_capital=alloc,
                risk_style=str(getattr(task, "risk_style", None) or "balanced"),
                account_available=vault_free,
                position_margin=p_margin,
                custom_prompt_enabled=custom_prompt_on,
                capital_usage_max_pct=usage_max,
                capital_usage_min_pct=usage_min,
            )
        )
    else:
        # 无资金仓（历史任务未设额度）→ 直接用主账户可用
        budget_margin = float(summary.get("available_margin") or 0)
    risk_style_for_filter = str(getattr(task, "risk_style", None) or "balanced")
    # AI 与决策模型都用模型置信度过风格门槛；纯量化规则置信度恒 1
    conf = (
        float(model_out.get("confidence") or 0)
        if strategy_type in ("ai", "decision")
        else 1.0
    )
    filter_custom_prompt = custom_prompt_on if strategy_type == "ai" else False

    filtered = filter_ai_action(
        action=str(model_out.get("action") or "hold"),
        quantity=int(model_out.get("quantity") or 0),
        side_mode=task.side_mode,
        position_mode=task.position_mode,
        fixed_qty=task.fixed_qty,
        position=position,
        available_margin=budget_margin,
        last_price=last_price,
        multiplier=mult,
        margin_rate=rate,
        close_ai_auto=close_ai,
        stop_ai_auto=stop_ai,
        risk_style=risk_style_for_filter,
        confidence=conf,
        custom_prompt_enabled=filter_custom_prompt,
        # 决策模型（Jev）：置信度本身是校准概率分布导出量，叠加策略先验后
        # 门槛取激进档 0.35（不沿用 balanced 0.5，避免开仓过死）
        min_confidence=0.35 if strategy_type == "decision" else None,
        qty_min=qty_min,
        qty_max=qty_max,
    )

    order_id, exec_msg = await execute_action(
        session,
        task,
        filtered["action"],
        int(filtered.get("quantity") or 0),
        last_price,
        redis_conn,
    )
    # 开仓成功记开仓时间；平仓成功清空
    if order_id and filtered["action"] in ("open_long", "open_short"):
        if getattr(task, "position_opened_at", None) is None:
            task.position_opened_at = _now_utc()
    if order_id and filtered["action"] == "close":
        task.position_opened_at = None
        await _apply_win_stats(session, task, order_id)
    reason = public_decision_reason(filtered, model_out, order_id, exec_msg)
    await _write_decision(
        session,
        task,
        bar_time=str((closed_bar or {}).get("time") or "") or None,
        trigger_type=trigger,
        context={
            "last_price": last_price,
            "position": position,
            "filtered": filtered,
            "exec_msg": exec_msg,
            "strategy_type": strategy_type,
            "risk_style": getattr(task, "risk_style", None),
            "allocated_capital": float(getattr(task, "allocated_capital", 0) or 0),
            "max_hold_days": int(getattr(task, "max_hold_days", None) or 10),
            "ma_snapshot": model_out.get("ma_snapshot"),
            "breakout_snapshot": model_out.get("breakout_snapshot"),
        },
        model_output=model_out,
        action=filtered["action"],
        order_id=order_id,
        reason=reason[:200],
    )
    # 信号级运行日志：分层 detail（因子快照 / 模型输出 / 账户）
    _rl_detail: dict[str, Any] = {
        "trigger_type": trigger,
        "last_price": last_price,
        "position": position,
        "filtered": {k: filtered.get(k) for k in ("action", "quantity")},
        "exec_msg": exec_msg,
        "account": {
            "total_equity": equity,
            "available_margin": summary.get("available_margin") if isinstance(summary, dict) else None,
        },
        "bars_used": len(signal_bars),
    }
    if strategy_type == "factor":
        _sp = str(getattr(task, "strategy_params", None) or "{}")
        _ctx_f = context.get("factor_signal") if isinstance(context, dict) else None
        try:
            import json as _json
            _params = _json.loads(_sp) if isinstance(_sp, str) else dict(_sp or {})
        except Exception:
            _params = {}
        _rl_detail["factor"] = {
            "signal": _ctx_f,
            "tokens": _params.get("factor_tokens"),
            "weights": _params.get("factor_weights"),
            "text": _params.get("factor_text"),
            "thresholds": {"entry": 0.3, "neutral": 0.05},
            # 平仓标准扩展（已规范化；None 表示未配置该条件）
            "indicator_exits": (task.close_rules or {}).get("indicator_exits"),
            "factor_exit": (task.close_rules or {}).get("factor_exit"),
        }
    if strategy_type == "ai":
        _rl_detail["model_output"] = model_out
        if isinstance(context, dict):
            _rl_detail["ai_context"] = {
                "factor_signal": context.get("factor_signal"),
                "quant_ref_signals": context.get("quant_ref_signals"),
                "risk_style": context.get("risk_style"),
                "hold_days": context.get("hold_days"),
            }
    if strategy_type == "decision":
        # 决策模型：完整 answers（choice/概率分布/usage）+ 挂载策略信号入日志便于审计
        _rl_detail["model_output"] = model_out
        if isinstance(context, dict):
            _rl_detail["strategy_signal"] = context.get("strategy_signal")
    await write_run_log(
        session, task,
        level="info",
        event="signal",
        action=filtered["action"],
        order_id=order_id,
        reason=reason[:200],
        detail=_rl_detail,
        bar_time=str((closed_bar or {}).get("time") or "") or None,
    )
    now = _now_utc()
    task.last_run_at = now
    task.last_bar_time = str((closed_bar or {}).get("time") or task.last_bar_time)
    task.next_bar_ts = next_bar_boundary(now, task.timeframe)
    task.updated_at = now
    task.note = None
    await session.flush()
    await snapshot_equity(session, task, redis_conn)
    return {
        "action": filtered["action"],
        "order_id": str(order_id) if order_id else None,
        "model": model_out,
    }
