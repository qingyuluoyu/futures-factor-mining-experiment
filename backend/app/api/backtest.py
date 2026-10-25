"""历史回测 API"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_session
from app.core.financial_request_route import FinancialRequestRoute
from app.models.user import User
from app.schemas.backtest import BacktestRunRequest
from app.schemas.synthetic import (
    SyntheticFinishRequest,
    SyntheticGenerateRequest,
    SyntheticStepRequest,
)
from app.services.ai_trading.strategies import (
    QUANT_STRATEGY_TYPES,
    STRATEGY_LABELS,
)
from app.services.backtest.runner import run_backtest
from app.services.backtest.synthetic import (
    finish_replay,
    generate_synthetic_series,
    step_decision,
)

logger = logging.getLogger("qihuo.backtest")
router = APIRouter(prefix="/api/backtest", tags=["历史回测"], route_class=FinancialRequestRoute)


@router.post("/run")
async def api_run_backtest(
    body: BacktestRunRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """同步执行回测并返回完整报告（最多约 6 个月）"""
    redis_conn = getattr(request.app.state, "redis", None)
    from app.services.quota.quota_svc import (
        FEATURE_BACKTEST,
        check_and_incr,
        get_effective_limits,
    )

    _limits = await get_effective_limits(session, current_user)
    await check_and_incr(
        redis_conn, current_user, FEATURE_BACKTEST, _limits["backtest_daily"], "历史回测"
    )
    payload = body.model_dump()
    try:
        return await run_backtest(
            session,
            redis_conn,
            current_user.id,
            payload,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("回测未捕获异常")
        raise HTTPException(
            status_code=500,
            detail=f"回测失败: {type(exc).__name__}: {exc}"[:300],
        ) from exc


@router.get("/limits")
async def api_backtest_limits(
    _user: User = Depends(get_current_user),
) -> dict:
    """回测约束说明（前端表单用）"""
    from app.services.backtest.data import (
        SEGMENT_MAX_COUNT,
        SEGMENT_MIN_COUNT,
        TIMEFRAME_MAX_DAYS,
    )

    return {
        "max_days": max(TIMEFRAME_MAX_DAYS.values()),
        "timeframe_max_days": dict(TIMEFRAME_MAX_DAYS),
        "timeframes": ["1m", "5m", "15m", "30m", "60m", "1d"],
        # 策略列表派生自注册表：新策略注册后自动出现在下拉（曾漏 strength 两档）
        "strategies": [
            *[{"value": k, "label": STRATEGY_LABELS[k]} for k in QUANT_STRATEGY_TYPES],
            {"value": "ai", "label": STRATEGY_LABELS["ai"]},
        ],
        "default_cash": 1_000_000,
        "ai_max_calls": 24,
        "segment_min_count": SEGMENT_MIN_COUNT,
        "segment_max_count": SEGMENT_MAX_COUNT,
        "notes": [
            "单段区间上限按周期：日线 5 年，分钟线 3~90 天",
            "多段回测（日线除外）：区间内随机抽取 2~5 段、每段为该周期上限天数，最小区间 = 段数×每段天数",
            "日线数据来自历史库+新浪；分钟线来自新浪近端窗口",
            "AI 回测对 K 线抽样调用模型，控制成本与耗时；多段时按段均分",
            "撮合按收盘价，含手续费与保证金约束，不接入真实模拟账户",
        ],
    }


@router.post("/generate-kline")
async def api_generate_synthetic_kline(
    body: SyntheticGenerateRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """AI 生成合成 K 线（LLM 剧本 + 程序化随机过程）

    不落库；返回完整 K 线序列与剧本，供前端边推边决策回放。
    不设业务使用次数配额；单次模型调用和超时保护仍生效。
    """
    redis_conn = getattr(request.app.state, "redis", None)
    from app.services.quota.quota_svc import (
        FEATURE_BACKTEST,
        check_and_incr,
        get_effective_limits,
    )

    _limits = await get_effective_limits(session, current_user)
    await check_and_incr(
        redis_conn, current_user, FEATURE_BACKTEST, _limits["backtest_daily"], "AI生成K线"
    )

    payload = body.model_dump()
    try:
        series = await generate_synthetic_series(
            session,
            current_user.id,
            model_row_id=str(payload.get("model_row_id") or ""),
            symbol=str(payload.get("symbol") or ""),
            symbol_name=str(payload.get("symbol_name") or ""),
            timeframe=str(payload.get("timeframe") or "1d"),
            num_days=int(payload.get("num_days") or 30),
            ref_price=payload.get("ref_price"),
            seed=payload.get("seed"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("AI 生成 K 线未捕获异常")
        raise HTTPException(
            status_code=500,
            detail=f"生成失败: {type(exc).__name__}: {exc}"[:300],
        ) from exc

    return {
        "session_id": uuid.uuid4().hex,
        "script": series.script.to_dict(),
        "bars": series.bars,
        "meta": series.meta,
    }


@router.post("/step")
async def api_synthetic_step(
    body: SyntheticStepRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """边推边决策：在最新一根 K 线收盘后请求一次策略决策。

    无状态：前端回传已可见的 K 线前缀 + 上一根账户快照，
    后端复用既有策略引擎（apply_hard_rules / quant_signal / VirtualAccount）
    计算当前 bar 的决策并回传最新账户快照与权益点。
    """
    payload = body.model_dump()
    try:
        return await step_decision(
            session,
            current_user.id,
            bars_so_far=list(payload.get("bars_so_far") or []),
            account_snapshot=payload.get("account_snapshot"),
            strategy_type=str(payload.get("strategy_type") or "n_breakout"),
            strategy_params=payload.get("strategy_params"),
            side_mode=str(payload.get("side_mode") or "both"),
            fixed_qty=int(payload.get("fixed_qty") or 1),
            initial_cash=float(payload.get("initial_cash") or 1_000_000),
            risk_style=str(payload.get("risk_style") or "balanced"),
            custom_prompt_enabled=bool(payload.get("custom_prompt_enabled") or False),
            custom_prompt=payload.get("custom_prompt"),
            close_rules=payload.get("close_rules"),
            stop_rules=payload.get("stop_rules"),
            max_hold_days=int(payload.get("max_hold_days") or 10),
            model_row_id=payload.get("model_row_id"),
            symbol=str(payload.get("symbol") or ""),
            symbol_name=str(payload.get("symbol_name") or ""),
            timeframe=str(payload.get("timeframe") or "1d"),
            decision_model_row_id=payload.get("decision_model_row_id"),
            decision_min_interval_ms=int(
                payload.get("decision_min_interval_ms") or 2000
            ),
            last_decision_at=payload.get("last_decision_at"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("step 决策未捕获异常")
        raise HTTPException(
            status_code=500,
            detail=f"决策失败: {type(exc).__name__}: {exc}"[:300],
        ) from exc


@router.post("/finish")
async def api_synthetic_finish(
    body: SyntheticFinishRequest,
    _current_user: User = Depends(get_current_user),
) -> dict:
    """回放结束：用与历史回测相同的 compute_metrics 算最终指标。

    返回结构与 run_backtest 完全一致，前端可直接复用 BacktestReportView。
    """
    payload = body.model_dump()
    try:
        return finish_replay(
            equity_curve=list(payload.get("equity_curve") or []),
            trades=list(payload.get("trades") or []),
            account_snapshot=payload.get("account_snapshot"),
            initial_cash=float(payload.get("initial_cash") or 1_000_000),
            bars=list(payload.get("bars") or []),
            strategy_type=str(payload.get("strategy_type") or "n_breakout"),
            ai_calls=int(payload.get("ai_calls") or 0),
            timeframe=str(payload.get("timeframe") or ""),
            symbol=str(payload.get("symbol") or ""),
            symbol_name=str(payload.get("symbol_name") or ""),
        )
    except Exception as exc:
        logger.exception("finish 指标计算未捕获异常")
        raise HTTPException(
            status_code=500,
            detail=f"指标计算失败: {type(exc).__name__}: {exc}"[:300],
        ) from exc
