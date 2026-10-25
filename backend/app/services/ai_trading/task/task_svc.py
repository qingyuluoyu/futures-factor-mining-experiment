"""AI 交易任务 CRUD 与状态机"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_model import AIModel
from app.models.ai_provider import AIProvider
from app.models.ai_trading.task import AITradingTask
from app.services.ai_trading.core.bar_align import SUPPORTED_TIMEFRAMES, next_bar_boundary
from app.services.ai_trading.equity import snapshot_equity
from app.services.paper.account_svc import get_account_summary

# 单用户同时 running 上限
MAX_RUNNING_TASKS = 5

_SIDE_MODES = {"long_only", "short_only", "both"}
_POS_MODES = {"full", "half", "fixed_qty", "scale_in"}


def _normalize_close_rules_exits(
    close_rules: dict[str, Any],
    entry_threshold: float | None = None,
) -> dict[str, Any]:
    """close_rules 扩展平仓标准（技术指标/因子阈值）规范化"""
    from app.services.ai_trading.core.exit_signals import (
        normalize_close_rules_exits,
    )

    return normalize_close_rules_exits(close_rules, entry_threshold)


def _factor_entry_threshold(
    strategy_type: str,
    strategy_params: dict[str, Any] | None,
) -> float | None:
    """取因子开仓线：factor 任务或 decision 挂 factor 策略；其余 None"""
    from app.services.ai_trading.strategies.factor import normalize_factor_params

    sp = strategy_params or {}
    if strategy_type == "factor":
        return normalize_factor_params(sp).get("entry_threshold")
    if strategy_type == "decision":
        ds = sp.get("decision_strategy") or {}
        if isinstance(ds, dict) and ds.get("kind") == "factor":
            return normalize_factor_params(ds.get("params")).get("entry_threshold")
    return None


def _normalize_ai_strategy_params(raw: dict[str, Any] | None) -> dict[str, Any]:
    """AI 任务 strategy_params 规范化：factor_tokens（int 列表）+ ref_strategies。

    ref_strategies 为多选量化策略参考（kind 白名单去重，见 quant_ref），
    供实时引擎与 AI 回测注入 prompt 作为 AI 决策参考信号。
    """
    from app.services.ai_trading.strategies.quant_ref import (
        normalize_ref_strategies,
    )

    src = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {}
    tokens = src.get("factor_tokens")
    if isinstance(tokens, list):
        cleaned: list[int] = []
        for t in tokens:
            try:
                cleaned.append(int(t))
            except (TypeError, ValueError):
                continue
        if cleaned:
            out["factor_tokens"] = cleaned
    refs = normalize_ref_strategies(src.get("ref_strategies"))
    if refs:
        out["ref_strategies"] = refs
    return out


def _normalize_decision_strategy(raw: dict[str, Any] | None) -> dict[str, Any]:
    """decision 任务 strategy_params 规范化：{decision_strategy:{kind, params}} 封装。

    混合模式：量化策略给决策模型喂内容（信号作为参考输入），
    决策模型做最终投资判断；kind 必须是量化策略类型，未传=纯模型。
    """
    from app.services.ai_trading.strategies import (
        QUANT_STRATEGY_TYPES,
        normalize_strategy_params,
    )

    src = raw if isinstance(raw, dict) else {}
    ds = src.get("decision_strategy")
    if not isinstance(ds, dict) or not ds.get("kind"):
        return {}
    kind = str(ds.get("kind") or "").strip().lower()
    if kind not in QUANT_STRATEGY_TYPES:
        raise HTTPException(
            status_code=400, detail=f"决策模型挂载的策略类型无效: {kind}"
        )
    return {
        "decision_strategy": {
            "kind": kind,
            "params": normalize_strategy_params(kind, ds.get("params"), strict=True),
        }
    }


def _quant_meta_name(
    strategy_type: str,
    params: dict[str, Any],
    symbol: str,
    timeframe: str,
) -> tuple[str, str, str]:
    """量化策略展示名 / provider / 默认任务名"""
    st = strategy_type
    if st == "ma_cross":
        display = f"MA{params['fast_period']}/{params['slow_period']}"
        provider = "量化·双均线"
        default = f"均线{params['fast_period']}/{params['slow_period']}·{symbol}·{timeframe}"
        return display, provider, default
    if st == "n_breakout":
        lb = int(params["lookback"])
        return f"突破{lb}日", "量化·N日突破", f"突破{lb}日·{symbol}·{timeframe}"
    if st == "macd_cross":
        display = (
            f"MACD{params['fast_period']}/"
            f"{params['slow_period']}/{params['signal_period']}"
        )
        return display, "量化·MACD", f"{display}·{symbol}·{timeframe}"
    if st == "kdj_cross":
        display = (
            f"KDJ{params['n_period']}/"
            f"{params['k_period']}/{params['d_period']}"
        )
        return display, "量化·KDJ", f"{display}·{symbol}·{timeframe}"
    if st == "band_swing":
        display = f"波段BOLL{params['period']}×{params['std_mult']}"
        return display, "量化·布林波段", f"{display}·{symbol}·{timeframe}"
    return st, "量化", f"{st}·{symbol}·{timeframe}"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


_QUANT_TYPES = (
    "ma_cross",
    "n_breakout",
    "macd_cross",
    "kdj_cross",
    "band_swing",
)


def _resolve_task_icon(task: AITradingTask) -> str | None:
    """任务头像 slug：显式 icon 优先，否则按策略类型给默认（ai=None 自动匹配）"""
    icon = getattr(task, "icon", None)
    if icon:
        return str(icon)
    st = str(getattr(task, "strategy_type", "") or "").lower()
    if st == "factor":
        return "factor"
    if st in _QUANT_TYPES:
        return "quant"
    return None


def _default_icon(body: dict[str, Any], strategy_type: str) -> str | None:
    """创建任务时的 icon：用户显式传入优先，否则按策略默认"""
    raw = str(body.get("icon") or "").strip()
    if raw:
        return raw[:48]
    if strategy_type == "factor":
        return "factor"
    if strategy_type in _QUANT_TYPES:
        return "quant"
    return None


def task_to_dict(
    task: AITradingTask,
    model_meta: dict[str, Any] | None = None,
    cash_delta: float | None = None,
) -> dict[str, Any]:
    """任务转响应 dict"""
    meta = model_meta or {}
    return {
        "id": str(task.id),
        "user_id": str(task.user_id),
        "name": task.name,
        "model_row_id": str(task.model_row_id) if task.model_row_id else None,
        "model_id": meta.get("model_id"),
        "model_display_name": meta.get("display_name"),
        "provider_name": meta.get("provider_name"),
        "strategy_type": getattr(task, "strategy_type", None) or "ai",
        "strategy_params": getattr(task, "strategy_params", None) or {},
        "icon": _resolve_task_icon(task),
        "trading_mode": getattr(task, "trading_mode", None) or "live",
        "symbol": task.symbol,
        "symbol_name": task.symbol_name,
        "timeframe": task.timeframe,
        "extra_timeframes": list(getattr(task, "extra_timeframes", None) or []),
        "ai_bars_limit": int(getattr(task, "ai_bars_limit", None) or 40),
        "decision_interval_sec": int(
            getattr(task, "decision_interval_sec", None) or 60
        ),
        "quant_interval_sec": int(getattr(task, "quant_interval_sec", 0) or 0),
        "signal_stop_price": (
            float(task.signal_stop_price)
            if getattr(task, "signal_stop_price", None) is not None
            else None
        ),
        "signal_stop_reverse_price": (
            float(task.signal_stop_reverse_price)
            if getattr(task, "signal_stop_reverse_price", None) is not None
            else None
        ),
        "signal_stop_src_time": getattr(task, "signal_stop_src_time", None),
        "side_mode": task.side_mode,
        "position_mode": task.position_mode,
        "fixed_qty": task.fixed_qty,
        "qty_min": int(getattr(task, "qty_min", None) or task.fixed_qty or 1),
        "qty_max": int(
            getattr(task, "qty_max", None)
            or getattr(task, "qty_min", None)
            or task.fixed_qty
            or 1
        ),
        "allocated_capital": float(getattr(task, "allocated_capital", 0) or 0),
        "capital_usage_min_pct": float(
            getattr(task, "capital_usage_min_pct", None) or 0
        ),
        "capital_usage_max_pct": float(
            getattr(task, "capital_usage_max_pct", None) or 100
        ),
        "risk_style": str(getattr(task, "risk_style", None) or "balanced"),
        "max_hold_days": int(getattr(task, "max_hold_days", None) or 10),
        "custom_prompt_enabled": bool(
            getattr(task, "custom_prompt_enabled", False)
        ),
        "custom_prompt": getattr(task, "custom_prompt", None),
        "position_opened_at": _iso(getattr(task, "position_opened_at", None)),
        # 胜率统计：每次平仓后增量更新（按平仓笔数计）
        "win_count": int(getattr(task, "win_count", 0) or 0),
        "loss_count": int(getattr(task, "loss_count", 0) or 0),
        "trade_count": int(getattr(task, "trade_count", 0) or 0),
        "win_rate": float(getattr(task, "win_rate", 0) or 0),
        "close_rules": task.close_rules or {},
        "stop_rules": task.stop_rules or {},
        "status": task.status,
        "pause_reason": task.pause_reason,
        "equity_baseline": float(task.equity_baseline or 0),
        "close_on_stop": bool(task.close_on_stop),
        "last_run_at": _iso(task.last_run_at),
        "next_bar_ts": _iso(task.next_bar_ts),
        "last_bar_time": task.last_bar_time,
        "started_at": _iso(task.started_at),
        "paused_at": _iso(task.paused_at),
        "stopped_at": _iso(task.stopped_at),
        "runtime_seconds": int(task.runtime_seconds or 0),
        "run_started_at": _iso(task.run_started_at),
        "note": task.note,
        "created_at": _iso(task.created_at) or "",
        "updated_at": _iso(task.updated_at) or "",
        "cash_delta": cash_delta,
        # 列表可编辑标记由 get/list 填充
        "can_edit": meta.get("can_edit"),
        "can_edit_rules": meta.get("can_edit_rules"),
        "has_orders": meta.get("has_orders"),
        "has_open_position": meta.get("has_open_position"),
        # 任务品种持仓快照（浮盈不含手续费）
        "position_direction": meta.get("position_direction"),
        "position_qty": meta.get("position_qty"),
        "position_avg_price": meta.get("position_avg_price"),
        "position_last_price": meta.get("position_last_price"),
        "position_unrealized": meta.get("position_unrealized"),
    }


async def _load_model_meta(
    session: AsyncSession,
    model_row_id: UUID | None,
    user_id: UUID,
) -> dict[str, Any]:
    if model_row_id is None:
        return {
            "model_id": None,
            "display_name": None,
            "provider_name": None,
        }
    stmt = (
        select(AIModel, AIProvider)
        .join(AIProvider, AIModel.provider_id == AIProvider.id)
        .where(AIModel.id == model_row_id, AIModel.user_id == user_id)
    )
    result = await session.execute(stmt)
    row = result.first()
    if row is None:
        raise HTTPException(status_code=400, detail="模型不存在或不属于当前用户")
    model, provider = row
    return {
        "model_id": model.model_id,
        "display_name": model.display_name,
        "provider_name": provider.name,
        "row": model,
        "provider": provider,
    }


def _validate_config(
    timeframe: str,
    side_mode: str,
    position_mode: str,
    fixed_qty: int,
) -> None:
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的周期，可选: {', '.join(SUPPORTED_TIMEFRAMES)}",
        )
    if side_mode not in _SIDE_MODES:
        raise HTTPException(status_code=400, detail="side_mode 无效")
    if position_mode not in _POS_MODES:
        raise HTTPException(status_code=400, detail="position_mode 无效")
    # 指定手数 / 滚仓都以 fixed_qty 为单位
    if position_mode in ("fixed_qty", "scale_in") and fixed_qty < 1:
        raise HTTPException(status_code=400, detail="手数/滚仓单位至少为 1")


def normalize_extra_timeframes(
    main_timeframe: str,
    raw: object,
    *,
    limit: int = 3,
) -> list[str]:
    """校验/规整多周期共振附加周期：合法值、去主周期、去重保序、最多 limit 个"""
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raise HTTPException(status_code=400, detail="extra_timeframes 必须为周期数组")
    result: list[str] = []
    for item in raw:
        tf = str(item or "").strip()
        if not tf:
            continue
        if tf not in SUPPORTED_TIMEFRAMES:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的周期 {tf}，可选: {', '.join(SUPPORTED_TIMEFRAMES)}",
            )
        if tf == main_timeframe or tf in result:
            continue
        result.append(tf)
    if len(result) > limit:
        raise HTTPException(
            status_code=400,
            detail=f"多周期共振最多 {limit} 个附加周期（加主周期共 {limit + 1} 个）",
        )
    return result


def normalize_ai_bars_limit(raw: object, default: int = 40) -> int:
    """AI 每周期 K 线根数：默认 40，夹到 [10, 240]"""
    try:
        val = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        val = default
    return max(10, min(240, val))


def normalize_quant_interval(raw: object, timeframe: str) -> int:
    """量化快频评估间隔秒：0=按 K 线收盘对齐（默认）；设了夹到 [60, 周期秒数]

    全部量化策略可用（decision 任务走 decision_interval_sec，不消费本字段）。
    上限即该任务 K 线周期（如 5m 任务最快 1 分钟、最慢 5 分钟）。
    未知周期（tf_sec=0）视为未启用，回落 0。
    """
    try:
        val = int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        val = 0
    if val <= 0:
        return 0
    from app.services.ai_trading.core.bar_align import timeframe_seconds

    tf_sec = timeframe_seconds(timeframe)
    if tf_sec <= 0:
        return 0
    return max(60, min(tf_sec, val))


async def _count_running(
    session: AsyncSession,
    user_id: UUID,
    trading_mode: str = "live",
) -> int:
    from app.core.trading_mode import normalize_trading_mode

    mode = normalize_trading_mode(trading_mode)
    stmt = select(func.count()).select_from(AITradingTask).where(
        AITradingTask.user_id == user_id,
        AITradingTask.trading_mode == mode,
        AITradingTask.status == "running",
    )
    result = await session.execute(stmt)
    return int(result.scalar_one() or 0)


async def create_task(
    session: AsyncSession,
    user_id: UUID,
    body: dict[str, Any],
    trading_mode: str = "live",
) -> dict[str, Any]:
    """创建任务，可选 auto_start；支持 AI 与多种量化策略"""
    from app.core.trading_mode import normalize_trading_mode
    from app.services.ai_trading.strategies import (
        QUANT_STRATEGY_TYPES,
        STRATEGY_LABELS,
        normalize_strategy_params,
    )
    from app.services.trading_hours import get_session_status

    mode = normalize_trading_mode(trading_mode)
    symbol = str(body.get("symbol") or "").strip().lower()
    from app.data.instrument_scope import ensure_cny_futures_symbol

    try:
        ensure_cny_futures_symbol(symbol)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    if not symbol:
        raise HTTPException(status_code=400, detail="合约代码不能为空")

    strategy_type = str(body.get("strategy_type") or "ai").strip().lower()
    allowed = ("ai", "decision") + QUANT_STRATEGY_TYPES
    if strategy_type not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"strategy_type 仅支持 {' / '.join(allowed)}",
        )

    # 决策模型评估间隔（60-300s，仅 decision 策略消费）
    decision_interval_sec = max(
        60, min(300, int(body.get("decision_interval_sec") or 60))
    )

    from app.services.ai_trading.core.capital import (
        max_hold_days_for_timeframe,
        normalize_capital_usage_pct,
        normalize_qty_range,
        normalize_risk_style,
        reserve_task_capital,
    )

    timeframe = str(body.get("timeframe") or "")
    side_mode = str(body.get("side_mode") or "both")
    position_mode = str(body.get("position_mode") or "fixed_qty")
    fixed_qty = int(body.get("fixed_qty") or 1)
    # 多周期共振 + AI 每周期 K 线根数（仅 AI 策略决策时消费，量化策略忽略）
    extra_timeframes = normalize_extra_timeframes(timeframe, body.get("extra_timeframes"))
    ai_bars_limit = normalize_ai_bars_limit(body.get("ai_bars_limit"))
    # 量化快频评估间隔（全部量化策略消费；0=按 K 线收盘对齐，
    # decision 任务不消费本字段，走 decision_interval_sec）
    quant_interval_sec = (
        normalize_quant_interval(body.get("quant_interval_sec"), timeframe)
        if strategy_type in QUANT_STRATEGY_TYPES
        else 0
    )
    qty_min_raw = body.get("qty_min")
    qty_max_raw = body.get("qty_max")
    qty_min, qty_max = normalize_qty_range(
        int(qty_min_raw) if qty_min_raw is not None else None,
        int(qty_max_raw) if qty_max_raw is not None else None,
        fixed_qty,
    )
    # 兼容：fixed_qty 与手数范围下限对齐
    fixed_qty = qty_min
    capital_usage_min_pct, capital_usage_max_pct = normalize_capital_usage_pct(
        body.get("capital_usage_min_pct"),
        body.get("capital_usage_max_pct"),
    )
    _validate_config(timeframe, side_mode, position_mode, fixed_qty)

    # 交易周期：用户传了用用户值（校验 1-365），否则按 K 线周期取默认
    user_hold_days = body.get("max_hold_days")
    if user_hold_days is not None:
        try:
            user_hold_days = int(user_hold_days)
        except (TypeError, ValueError):
            user_hold_days = None
    if user_hold_days is not None and 1 <= user_hold_days <= 365:
        max_hold_days = user_hold_days
    else:
        max_hold_days = max_hold_days_for_timeframe(timeframe)
    # 量化：不占用 AI 资金仓；AI：必须划拨额度
    if strategy_type == "ai":
        risk_style = normalize_risk_style(
            str(body.get("risk_style") or "balanced")
        )
        try:
            allocated_capital = Decimal(
                str(body.get("allocated_capital") or 100000)
            )
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail="allocated_capital 无效"
            ) from exc
        custom_prompt_enabled = bool(body.get("custom_prompt_enabled") or False)
        custom_prompt_raw = body.get("custom_prompt")
        custom_prompt = (
            str(custom_prompt_raw).strip()[:2000] if custom_prompt_raw else None
        )
        if custom_prompt_enabled and not custom_prompt:
            raise HTTPException(
                status_code=400, detail="已启用用户提示词但内容为空"
            )
    else:
        risk_style = "balanced"
        allocated_capital = Decimal(str(body.get("allocated_capital") or 0))
        custom_prompt_enabled = False
        custom_prompt = None

    model_row_id: UUID | None = None
    meta: dict[str, Any]
    strategy_params: dict[str, Any] = {}

    if strategy_type == "ai":
        try:
            model_row_id = UUID(str(body["model_row_id"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail="AI 策略必须选择模型") from exc
        # AI 任务持久化 strategy_params（factor_tokens + ref_strategies 多选参考），
        # 供引擎注入参考信号；此前被丢弃导致因子挂载不生效
        strategy_params = _normalize_ai_strategy_params(body.get("strategy_params"))
        meta = await _load_model_meta(session, model_row_id, user_id)
        # 决策模型（Jev）只能在量化交易·决策模式使用，AI 交易不可选
        if str(getattr(meta.get("provider"), "api_type", "") or "") == "jev":
            raise HTTPException(
                status_code=400,
                detail="决策模型（Jev）不支持对话式 AI 交易，请在「创建量化交易」里勾选决策模型使用",
            )
        default_name = f"{meta['display_name']}·{symbol}·{timeframe}"
    elif strategy_type == "decision":
        try:
            model_row_id = UUID(str(body["model_row_id"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=400, detail="决策模型任务必须选择模型"
            ) from exc
        meta = await _load_model_meta(session, model_row_id, user_id)
        # decision 任务只接受 TypeSafe Jev 渠道（api_type=jev）的模型
        if str(getattr(meta.get("provider"), "api_type", "") or "") != "jev":
            raise HTTPException(
                status_code=400,
                detail="决策模型任务只能选择 TypeSafe Jev 渠道的模型",
            )
        # 混合模式：量化策略给决策模型喂内容；未挂策略=纯模型独立判断
        strategy_params = _normalize_decision_strategy(body.get("strategy_params"))
        ds = strategy_params.get("decision_strategy")
        strat_tag = ""
        if ds:
            display, _, _ = _quant_meta_name(
                ds["kind"], ds["params"], symbol, timeframe
            )
            strat_tag = f"{display}·"
        default_name = f"{meta['display_name']}·{strat_tag}{symbol}·{timeframe}"
    else:
        strategy_params = normalize_strategy_params(
            strategy_type, body.get("strategy_params"), strict=True
        )
        display, provider, default_name = _quant_meta_name(
            strategy_type, strategy_params, symbol, timeframe
        )
        meta = {
            "model_id": None,
            "display_name": display,
            "provider_name": provider or STRATEGY_LABELS.get(strategy_type, "量化"),
        }

    name = str(body.get("name") or "").strip() or default_name

    close_rules = body.get("close_rules") or {}
    stop_rules = body.get("stop_rules") or {}
    if hasattr(close_rules, "model_dump"):
        close_rules = close_rules.model_dump()
    if hasattr(stop_rules, "model_dump"):
        stop_rules = stop_rules.model_dump()
    # 指标/因子平仓扩展字段规范化（kind 白名单、参数钳制、去重）
    close_rules = _normalize_close_rules_exits(
        dict(close_rules),
        _factor_entry_threshold(strategy_type, strategy_params),
    )
    stop_rules = dict(stop_rules)
    # 量化强制关闭 AI 自主平仓/止损，改由规则信号驱动
    if strategy_type in QUANT_STRATEGY_TYPES:
        close_rules["ai_auto"] = False
        stop_rules["ai_auto"] = False

    summary = await get_account_summary(session, user_id, mode)
    baseline = Decimal(str(summary.get("total_equity") or 0))
    now = _now_utc()
    auto_start = bool(body.get("auto_start"))
    # auto_start 时校验运行任务配额：admin 不限；普通用户用生效配额
    if auto_start:
        from app.models.user import User
        from app.services.quota.quota_svc import get_effective_limits

        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if user is not None:
            limits = await get_effective_limits(session, user)
            max_running = limits.get("max_tasks")
            if max_running is not None:
                running = await _count_running(session, user_id, mode)
                if running >= int(max_running):
                    raise HTTPException(
                        status_code=400,
                        detail=f"同时运行任务不能超过 {max_running} 个",
                    )

    symbol_open = bool(get_session_status(symbol).get("is_open"))
    if auto_start and symbol_open:
        init_status = "running"
        init_pause_reason = None
        init_note = None
    elif auto_start and not symbol_open:
        init_status = "paused"
        init_pause_reason = "market_closed"
        init_note = "非交易时段已挂起，开盘后自动开始"
    else:
        init_status = "paused"
        init_pause_reason = "user"
        init_note = None

    task = AITradingTask(
        user_id=user_id,
        trading_mode=mode,
        name=name,
        model_row_id=model_row_id,
        strategy_type=strategy_type,
        strategy_params=strategy_params,
        icon=_default_icon(body, strategy_type),
        symbol=symbol,
        symbol_name=str(body.get("symbol_name") or ""),
        timeframe=timeframe,
        extra_timeframes=extra_timeframes,
        ai_bars_limit=ai_bars_limit,
        decision_interval_sec=decision_interval_sec,
        quant_interval_sec=quant_interval_sec,
        side_mode=side_mode,
        position_mode=position_mode,
        fixed_qty=fixed_qty,
        qty_min=qty_min,
        qty_max=qty_max,
        capital_usage_min_pct=Decimal(str(capital_usage_min_pct)),
        capital_usage_max_pct=Decimal(str(capital_usage_max_pct)),
        allocated_capital=allocated_capital,
        risk_style=risk_style,
        max_hold_days=max_hold_days,
        custom_prompt_enabled=custom_prompt_enabled,
        custom_prompt=custom_prompt,
        position_opened_at=None,
        close_rules=dict(close_rules),
        stop_rules=dict(stop_rules),
        status=init_status,
        pause_reason=init_pause_reason,
        equity_baseline=baseline,
        close_on_stop=bool(body.get("close_on_stop", True)),
        started_at=now if auto_start else None,
        paused_at=now if init_status == "paused" and auto_start else None,
        run_started_at=now if init_status == "running" else None,
        next_bar_ts=next_bar_boundary(now, timeframe) if init_status == "running" else None,
        note=init_note,
        created_at=now,
        updated_at=now,
    )
    session.add(task)
    await session.flush()
    # 所有策略（AI/量化/因子）都预留资金仓
    if allocated_capital > 0:
        await reserve_task_capital(session, task, allocated_capital)
    if auto_start:
        await snapshot_equity(session, task, None)
    return task_to_dict(task, meta)


async def list_tasks(
    session: AsyncSession,
    user_id: UUID,
    redis_conn: object | None = None,
    trading_mode: str = "live",
) -> list[dict[str, Any]]:
    """用户任务列表（限定 trading_mode）

    cash_delta = 本任务品种浮动盈亏（不含手续费），避免共用账户串扰。
    """
    from app.core.trading_mode import normalize_trading_mode
    from app.services.ai_trading.task.position_pnl import (
        get_task_position_pnl,
        source_for_task,
    )

    mode = normalize_trading_mode(trading_mode)
    stmt = (
        select(AITradingTask)
        .where(
            AITradingTask.user_id == user_id,
            AITradingTask.trading_mode == mode,
        )
        .order_by(AITradingTask.created_at.desc())
    )
    result = await session.execute(stmt)
    tasks = list(result.scalars().all())
    out: list[dict[str, Any]] = []
    for task in tasks:
        try:
            meta = await _load_model_meta(session, task.model_row_id, user_id)
        except HTTPException:
            meta = {}
        flags = await _task_trade_flags(session, task)
        pnl = await get_task_position_pnl(
            session,
            user_id,
            task.symbol,
            redis_conn,
            source_for_task(task),
            task_id=task.id,
        )
        meta = {
            **meta,
            **flags,
            "position_direction": pnl.get("direction"),
            "position_qty": pnl.get("quantity") or 0,
            "position_avg_price": pnl.get("avg_price"),
            "position_last_price": pnl.get("last_price"),
            "position_unrealized": pnl.get("unrealized_pnl") or 0.0,
        }
        # 有持仓用浮盈；无持仓用 0（不把账户手续费摊到未开仓任务）
        delta = float(pnl.get("cash_delta") or 0.0)
        out.append(task_to_dict(task, meta, cash_delta=delta))
    return out


async def delete_task(
    session: AsyncSession,
    user_id: UUID,
    task_id: UUID,
    trading_mode: str = "live",
) -> dict[str, Any]:
    """删除已结束且无持仓的任务（decisions/equity 级联删除，订单保留）"""
    from app.core.trading_mode import normalize_trading_mode
    from app.models.paper.position import PaperPosition

    mode = normalize_trading_mode(trading_mode)
    task = (
        await session.execute(
            select(AITradingTask).where(
                AITradingTask.id == task_id,
                AITradingTask.user_id == user_id,
                AITradingTask.trading_mode == mode,
            )
        )
    ).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status != "stopped":
        raise HTTPException(
            status_code=400, detail="仅已结束任务可删除，请先结束任务"
        )

    # 保险校验：该任务无持仓（stop 默认已平仓）；按 task_id 精确区分本任务
    pos = (
        await session.execute(
            select(PaperPosition)
            .where(
                PaperPosition.user_id == user_id,
                PaperPosition.symbol == task.symbol,
                PaperPosition.quantity > 0,
                PaperPosition.task_id == task.id,
            )
            .limit(1)
        )
    ).first()
    if pos is not None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"该任务仍有 {pos[0].quantity} 手 {pos[0].symbol} "
                f"({pos[0].direction}) 持仓未平，无法删除。"
                "请在开盘期间先结束任务平仓，或手动平仓后再删除。"
            ),
        )
    # 删除前退回资金仓余额到主账户
    from app.services.ai_trading.core.capital import release_task_capital

    if Decimal(str(getattr(task, "capital_balance", 0) or 0)) > 0:
        await release_task_capital(session, task)
    symbol = task.symbol
    await session.delete(task)
    await session.commit()
    return {"id": str(task_id), "symbol": symbol, "deleted": True}


async def _task_trade_flags(
    session: AsyncSession,
    task: AITradingTask,
) -> dict[str, bool]:
    """任务是否已下单、该品种是否仍有持仓"""
    from app.models.ai_trading.decision import AITradingDecision
    from app.models.paper.position import PaperPosition

    order_stmt = (
        select(func.count())
        .select_from(AITradingDecision)
        .where(
            AITradingDecision.task_id == task.id,
            AITradingDecision.order_id.is_not(None),
        )
    )
    has_orders = int((await session.execute(order_stmt)).scalar_one() or 0) > 0

    pos_stmt = select(PaperPosition).where(
        PaperPosition.user_id == task.user_id,
        PaperPosition.symbol == task.symbol,
        PaperPosition.quantity > 0,
        PaperPosition.task_id == task.id,
    )
    pos = (await session.execute(pos_stmt)).scalar_one_or_none()
    has_open_position = pos is not None and int(pos.quantity or 0) > 0
    # 可改：已结束，且（从未下单 或 已全部平仓）
    can_edit = task.status == "stopped" and (not has_orders or not has_open_position)
    # 盈亏比例调整：无持仓即可（运行/暂停/已结束均可，运行中下轮评估生效）
    can_edit_rules = not has_open_position
    return {
        "has_orders": has_orders,
        "has_open_position": has_open_position,
        "can_edit": can_edit,
        "can_edit_rules": can_edit_rules,
    }


async def get_task(
    session: AsyncSession,
    user_id: UUID,
    task_id: UUID,
    redis_conn: object | None = None,
    trading_mode: str = "live",
) -> tuple[AITradingTask, dict[str, Any]]:
    """获取任务实体 + 响应 dict（分盘）"""
    from app.core.trading_mode import normalize_trading_mode
    from app.services.ai_trading.task.position_pnl import (
        get_task_position_pnl,
        source_for_task,
    )

    mode = normalize_trading_mode(trading_mode)
    stmt = select(AITradingTask).where(
        AITradingTask.id == task_id,
        AITradingTask.user_id == user_id,
        AITradingTask.trading_mode == mode,
    )
    result = await session.execute(stmt)
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    try:
        meta = await _load_model_meta(session, task.model_row_id, user_id)
    except HTTPException:
        meta = {}
    flags = await _task_trade_flags(session, task)
    pnl = await get_task_position_pnl(
        session,
        user_id,
        task.symbol,
        redis_conn,
        source_for_task(task),
        task_id=task.id,
    )
    meta = {
        **meta,
        **flags,
        "position_direction": pnl.get("direction"),
        "position_qty": pnl.get("quantity") or 0,
        "position_avg_price": pnl.get("avg_price"),
        "position_last_price": pnl.get("last_price"),
        "position_unrealized": pnl.get("unrealized_pnl") or 0.0,
    }
    return task, task_to_dict(
        task, meta, cash_delta=float(pnl.get("cash_delta") or 0.0)
    )


async def update_task(
    session: AsyncSession,
    user_id: UUID,
    task_id: UUID,
    body: dict[str, Any],
) -> dict[str, Any]:
    """更新配置：仅已结束且（未下单或已全部平仓）可改"""
    task, data = await get_task(session, user_id, task_id)
    if task.status != "stopped":
        raise HTTPException(
            status_code=400,
            detail="仅已结束的任务可修改，请先结束任务",
        )
    if data.get("has_open_position"):
        raise HTTPException(
            status_code=400,
            detail="该品种仍有持仓，请全部平仓后再修改",
        )
    # has_orders 且有持仓已拦；无持仓（含从未下单）可改

    if body.get("name") is not None:
        task.name = str(body["name"]).strip() or task.name

    stype = str(getattr(task, "strategy_type", None) or "ai")
    if stype == "ai" and body.get("model_row_id") is not None:
        try:
            model_row_id = UUID(str(body["model_row_id"]))
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail="model_row_id 无效") from exc
        new_meta = await _load_model_meta(session, model_row_id, user_id)
        if str(getattr(new_meta.get("provider"), "api_type", "") or "") == "jev":
            raise HTTPException(
                status_code=400,
                detail="决策模型（Jev）不支持对话式 AI 交易",
            )
        task.model_row_id = model_row_id
    if stype == "decision" and body.get("model_row_id") is not None:
        try:
            model_row_id = UUID(str(body["model_row_id"]))
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail="model_row_id 无效") from exc
        new_meta = await _load_model_meta(session, model_row_id, user_id)
        if str(getattr(new_meta.get("provider"), "api_type", "") or "") != "jev":
            raise HTTPException(
                status_code=400,
                detail="决策模型任务只能选择 TypeSafe Jev 渠道的模型",
            )
        task.model_row_id = model_row_id

    if body.get("strategy_params") is not None:
        from app.services.ai_trading.strategies import (
            QUANT_STRATEGY_TYPES,
            normalize_strategy_params,
        )

        if stype in QUANT_STRATEGY_TYPES:
            task.strategy_params = normalize_strategy_params(
                stype, body.get("strategy_params"), strict=True
            )
        elif stype == "ai":
            task.strategy_params = _normalize_ai_strategy_params(
                body.get("strategy_params")
            )
        elif stype == "decision":
            task.strategy_params = _normalize_decision_strategy(
                body.get("strategy_params")
            )

    if body.get("icon") is not None:
        raw_icon = str(body.get("icon") or "").strip()
        task.icon = raw_icon[:48] or None

    # 决策模型评估间隔（仅 decision 策略可改）
    if stype == "decision" and body.get("decision_interval_sec") is not None:
        try:
            task.decision_interval_sec = max(
                60, min(300, int(body["decision_interval_sec"]))
            )
        except (TypeError, ValueError):
            pass

    # 量化快频评估间隔（全部量化策略可改；decision 不消费本字段；
    # 按任务当前周期钳制，同请求改周期时引擎侧还有 [60, 周期秒数] 二次钳制兜底）
    if stype in QUANT_STRATEGY_TYPES and body.get("quant_interval_sec") is not None:
        try:
            task.quant_interval_sec = normalize_quant_interval(
                body["quant_interval_sec"], str(task.timeframe or "")
            )
        except (TypeError, ValueError):
            pass

    if body.get("symbol") is not None:
        symbol = str(body["symbol"]).strip().lower()
        if not symbol:
            raise HTTPException(status_code=400, detail="合约代码不能为空")
        task.symbol = symbol
    if body.get("symbol_name") is not None:
        task.symbol_name = str(body["symbol_name"] or "")

    if body.get("timeframe") is not None:
        tf = str(body["timeframe"])
        if tf not in SUPPORTED_TIMEFRAMES:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的周期，可选: {', '.join(SUPPORTED_TIMEFRAMES)}",
            )
        task.timeframe = tf
        # 主周期变更：附加周期中与新主周期相同的自动剔除
        cur_extras = list(getattr(task, "extra_timeframes", None) or [])
        if tf in cur_extras:
            task.extra_timeframes = [x for x in cur_extras if x != tf]

    # 多周期共振：显式传数组（含空数组=清空），未传=不变
    if body.get("extra_timeframes") is not None:
        task.extra_timeframes = normalize_extra_timeframes(
            task.timeframe, body.get("extra_timeframes")
        )
    if body.get("ai_bars_limit") is not None:
        task.ai_bars_limit = normalize_ai_bars_limit(body.get("ai_bars_limit"))

    if body.get("side_mode") is not None:
        if body["side_mode"] not in _SIDE_MODES:
            raise HTTPException(status_code=400, detail="side_mode 无效")
        task.side_mode = body["side_mode"]
    if body.get("position_mode") is not None:
        if body["position_mode"] not in _POS_MODES:
            raise HTTPException(status_code=400, detail="position_mode 无效")
        task.position_mode = body["position_mode"]
    if body.get("fixed_qty") is not None:
        task.fixed_qty = int(body["fixed_qty"])
    if (
        body.get("qty_min") is not None
        or body.get("qty_max") is not None
        or body.get("fixed_qty") is not None
    ):
        from app.services.ai_trading.core.capital import normalize_qty_range

        cur_min = int(getattr(task, "qty_min", None) or task.fixed_qty or 1)
        cur_max = int(getattr(task, "qty_max", None) or cur_min)
        qmin, qmax = normalize_qty_range(
            int(body["qty_min"]) if body.get("qty_min") is not None else cur_min,
            int(body["qty_max"]) if body.get("qty_max") is not None else cur_max,
            int(body["fixed_qty"]) if body.get("fixed_qty") is not None else task.fixed_qty,
        )
        task.qty_min = qmin
        task.qty_max = qmax
        task.fixed_qty = qmin
    if (
        body.get("capital_usage_min_pct") is not None
        or body.get("capital_usage_max_pct") is not None
    ):
        from app.services.ai_trading.core.capital import normalize_capital_usage_pct

        lo, hi = normalize_capital_usage_pct(
            body.get("capital_usage_min_pct", getattr(task, "capital_usage_min_pct", 0)),
            body.get("capital_usage_max_pct", getattr(task, "capital_usage_max_pct", 100)),
        )
        task.capital_usage_min_pct = Decimal(str(lo))
        task.capital_usage_max_pct = Decimal(str(hi))
    if body.get("allocated_capital") is not None:
        from app.services.ai_trading.core.capital import (
            release_task_capital,
            reserve_task_capital,
        )

        try:
            amount = Decimal(str(body["allocated_capital"]))
        except Exception as exc:
            raise HTTPException(status_code=400, detail="allocated_capital 无效") from exc
        # 差额调整：先退旧资金仓余额，再划新额度
        if Decimal(str(task.capital_balance or 0)) > 0:
            await release_task_capital(session, task)
        task.allocated_capital = amount
        if amount > 0:
            await reserve_task_capital(session, task, amount)
    if body.get("risk_style") is not None:
        from app.services.ai_trading.core.capital import normalize_risk_style

        task.risk_style = normalize_risk_style(str(body["risk_style"]))
    # 交易周期：用户显式传了才更新；若改了 timeframe 但没传周期，保持用户已有周期值
    if body.get("max_hold_days") is not None:
        try:
            new_hold = int(body["max_hold_days"])
            if 1 <= new_hold <= 365:
                task.max_hold_days = new_hold
        except (TypeError, ValueError):
            pass
    if body.get("custom_prompt_enabled") is not None:
        task.custom_prompt_enabled = bool(body["custom_prompt_enabled"])
    if "custom_prompt" in body:
        raw = body.get("custom_prompt")
        task.custom_prompt = str(raw).strip()[:2000] if raw else None
    if task.custom_prompt_enabled and not (task.custom_prompt or "").strip():
        if stype == "ai":
            raise HTTPException(status_code=400, detail="已启用用户提示词但内容为空")
    if stype != "ai":
        task.custom_prompt_enabled = False
        task.custom_prompt = None
    if body.get("close_rules") is not None:
        rules = body["close_rules"]
        close_rules = (
            rules.model_dump() if hasattr(rules, "model_dump") else dict(rules)
        )
        # 指标/因子平仓扩展字段规范化
        close_rules = _normalize_close_rules_exits(
            close_rules,
            _factor_entry_threshold(
                str(getattr(task, "strategy_type", "") or ""),
                getattr(task, "strategy_params", None) or {},
            ),
        )
        # 量化策略禁止 AI 自主平仓语义
        from app.services.ai_trading.strategies import QUANT_STRATEGY_TYPES

        if stype in QUANT_STRATEGY_TYPES:
            close_rules["ai_auto"] = False
        task.close_rules = close_rules
    if body.get("stop_rules") is not None:
        rules = body["stop_rules"]
        stop_rules = (
            rules.model_dump() if hasattr(rules, "model_dump") else dict(rules)
        )
        from app.services.ai_trading.strategies import QUANT_STRATEGY_TYPES

        if stype in QUANT_STRATEGY_TYPES:
            stop_rules["ai_auto"] = False
        task.stop_rules = stop_rules
    if body.get("close_on_stop") is not None:
        task.close_on_stop = bool(body["close_on_stop"])

    # 修改后回到 paused，便于再次「开始」
    task.status = "paused"
    task.pause_reason = "user"
    task.stopped_at = None
    task.paused_at = _now_utc()
    task.last_bar_time = None
    task.next_bar_ts = None
    task.note = "配置已修改，可重新开始"
    task.updated_at = _now_utc()
    await session.flush()
    _, data = await get_task(session, user_id, task_id)
    return data


async def update_task_rules(
    session: AsyncSession,
    user_id: UUID,
    task_id: UUID,
    body: dict[str, Any],
    trading_mode: str = "live",
) -> dict[str, Any]:
    """无持仓时调整止盈/止损规则（running/paused/stopped 均可）。

    与 update_task 不同：
    - 不要求 status == stopped，运行/暂停中即可调整盈亏比例
    - 仅更新 close_rules / stop_rules，不触碰其它交易配置
    - 不改变任务状态；loop 每轮从 DB 重读 running 任务，
      新规则下一轮评估直接生效
    """
    task, data = await get_task(
        session, user_id, task_id, trading_mode=trading_mode
    )
    if data.get("has_open_position"):
        raise HTTPException(
            status_code=400,
            detail="该品种仍有持仓，请平仓后再调整盈亏比例",
        )

    stype = str(getattr(task, "strategy_type", None) or "ai")
    from app.services.ai_trading.strategies import QUANT_STRATEGY_TYPES

    if body.get("close_rules") is not None:
        rules = body["close_rules"]
        close_rules = (
            rules.model_dump() if hasattr(rules, "model_dump") else dict(rules)
        )
        # 指标/因子平仓扩展字段规范化（新规则下一轮评估直接生效）
        close_rules = _normalize_close_rules_exits(
            close_rules,
            _factor_entry_threshold(
                str(getattr(task, "strategy_type", "") or ""),
                getattr(task, "strategy_params", None) or {},
            ),
        )
        # 量化策略禁止 AI 自主平仓语义
        if stype in QUANT_STRATEGY_TYPES:
            close_rules["ai_auto"] = False
        task.close_rules = close_rules
    if body.get("stop_rules") is not None:
        rules = body["stop_rules"]
        stop_rules = (
            rules.model_dump() if hasattr(rules, "model_dump") else dict(rules)
        )
        if stype in QUANT_STRATEGY_TYPES:
            stop_rules["ai_auto"] = False
        task.stop_rules = stop_rules

    task.updated_at = _now_utc()
    await session.flush()
    _, fresh = await get_task(
        session, user_id, task_id, trading_mode=trading_mode
    )
    return fresh


async def switch_task_model(
    session: AsyncSession,
    user_id: UUID,
    task_id: UUID,
    model_row_id: str,
    redis_conn: object | None = None,
    trading_mode: str = "live",
) -> dict[str, Any]:
    """切换任务模型并立即用新模型执行一次（接手操盘）。

    与 update_task 不同：
    - 不要求 status == stopped（running/paused 均可）
    - 仅更新 model_row_id，不触碰交易配置（品种/资金/策略规则不变）
    - 切换后立即 evaluate_task(force=True) 用新模型评估当前持仓

    仅对 strategy_type == 'ai' 的任务有效；量化任务无模型，拒绝。
    """
    from app.services.ai_trading.engine import evaluate_task

    # 校验任务归属当前盘，拿到 ORM 实体
    task, before = await get_task(
        session, user_id, task_id, redis_conn, trading_mode
    )

    stype = str(getattr(task, "strategy_type", None) or "ai").strip().lower()
    if stype != "ai":
        raise HTTPException(
            status_code=400,
            detail="量化策略任务不使用 AI 模型，无法接手",
        )

    # 校验新模型合法且属于当前用户
    try:
        new_model_row_id = UUID(str(model_row_id))
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="model_row_id 无效") from exc
    meta = await _load_model_meta(session, new_model_row_id, user_id)

    # AI 任务不可切换到决策模型（Jev）：决策模型仅量化交易·decision 策略可用
    if str(getattr(meta.get("provider"), "api_type", "") or "") == "jev":
        raise HTTPException(
            status_code=400,
            detail="决策模型（Jev）不支持对话式 AI 交易，无法接手",
        )

    old_display = before.get("model_display_name") or before.get("name") or "未知"
    new_display = meta.get("display_name") or "未知"

    # 仅更新模型字段，不触碰交易配置
    task.model_row_id = new_model_row_id
    task.updated_at = _now_utc()
    task.note = f"模型接手：{old_display} → {new_display}"
    await session.flush()

    # 立即用新模型执行一次（force=True 忽略 bar 对齐）
    decision: dict[str, Any] = {}
    try:
        decision = await evaluate_task(session, task, redis_conn, force=True)
    except Exception:
        # 接手评估失败不回滚模型切换，记录异常供用户查看
        decision = {"action": "hold", "reason": "接手评估异常，请查看日志"}

    await session.commit()
    _, data = await get_task(session, user_id, task_id, redis_conn, trading_mode)
    return {"task": data, "decision": decision}
