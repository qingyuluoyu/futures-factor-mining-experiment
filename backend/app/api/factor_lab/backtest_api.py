"""因子实验室 —— 回测与 A/B 对比"""

from __future__ import annotations

import asyncio

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.factor_lab.common import load_bars, reject_legacy_tokens, resolve_cost
from app.core.deps import get_current_user, get_session
from app.models.user import User
from app.schemas.factor_lab import BacktestFactorRequest, CompareRequest
from app.services.factor_lab import execute
from app.services.factor_lab.features import feature_matrix
from app.services.factor_lab.scoring.evaluate import (
    evaluate_factor,
    evaluate_factor_live,
    next_ret,
    position_from_factor,
)
from app.services.factor_lab.scoring.periods import bars_per_year

router = APIRouter()


from app.services.factor_lab.aux_attach import (
    attach_aux_for_tokens,
    collect_factor_tokens,
    tokens_need_attach,
)


async def _attach_aux(session, symbol: str, timeframe: str, bars: list[dict], tokens) -> list[dict]:
    """含扩展信息源 token 的公式:bars 挂上板块动量 x_* 后特征矩阵才有扩展行"""
    flat = collect_factor_tokens({"factor_tokens": tokens})
    if not tokens_need_attach(flat):
        return bars
    return await attach_aux_for_tokens(session, symbol, timeframe, bars, flat)


@router.post("/backtest-factor")
async def api_backtest_factor(
    body: BacktestFactorRequest,
    request: Request,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """单因子资金曲线回测"""
    redis_conn = getattr(request.app.state, "redis", None)
    from app.services.quota.quota_svc import (
        FEATURE_FACTOR,
        check_and_incr,
        get_effective_limits,
    )

    _limits = await get_effective_limits(session, _user)
    await check_and_incr(
        redis_conn, _user, FEATURE_FACTOR, _limits["factor_daily"], "因子实验室"
    )
    bars = await load_bars(
        redis_conn,
        session,
        body.symbol,
        body.timeframe,
        body.start_date,
        body.end_date,
    )
    reject_legacy_tokens([body.factor_tokens])
    bars = await _attach_aux(session, body.symbol, body.timeframe, bars, body.factor_tokens)
    mat = feature_matrix(bars)
    factor = execute(body.factor_tokens, mat)
    if factor is None:
        raise HTTPException(status_code=400, detail="因子公式无效或无法执行")
    close = np.array([float(b.get("close") or 0) for b in bars], dtype=float)
    cost = resolve_cost(body.symbol, bars, body.cost)
    periods = bars_per_year(bars, body.timeframe)
    metrics = evaluate_factor(factor, close, cost=cost, periods=periods)
    # 实盘离散口径（±1 手、0.3 入场/0.05 平仓）：回测连续仓位无法在实盘
    # 复现，同步给出可实现指标，消除"回测好实盘差"的口径盲区
    live_metrics = evaluate_factor_live(factor, close, cost=cost, periods=periods)
    pos = position_from_factor(factor)
    ret = next_ret(close)
    # P1-6:与 evaluate_factor 完全同式——prev[0]=0(首根保留建仓成本,不用
    # 环绕的 pos[-1]);资金曲线右移一根,第 t 根显示的是 t-1 决策已实现的
    # 盈亏(原实现把 i→i+1 的收益标在 i 上,图形上"信号一出当根就赚钱")
    prev = np.roll(pos, 1)
    prev[0] = 0.0
    pnl = pos * ret - np.abs(pos - prev) * cost
    realized = np.roll(pnl, 1)
    realized[0] = 0.0
    equity = body.initial_cash * (1.0 + np.cumsum(realized))

    # 可选 walk-forward 视图：展示因子在不同时点偏移下的表现明细，
    # 让用户单因子也能直观看出"推移到哪些时段会亏"（对应你 7-31 后亏损的现象）
    wf_detail = None
    if body.walk_forward_folds > 0:
        from app.services.factor_lab.scoring.walk_forward import walk_forward_eval

        wf_detail = walk_forward_eval(
            body.factor_tokens, bars, body.timeframe, cost, body.walk_forward_folds
        )

    n = len(bars)
    step = max(1, n // 300)
    curve = [
        {
            "time": str(bars[i].get("time") or ""),
            "equity": round(float(equity[i]), 2),
            "position": round(float(pos[i]), 4),
            "price": round(float(close[i]), 4),
        }
        for i in range(0, n, step)
    ]
    if (n - 1) % step:
        curve.append(
            {
                "time": str(bars[-1].get("time") or ""),
                "equity": round(float(equity[-1]), 2),
                "position": round(float(pos[-1]), 4),
                "price": round(float(close[-1]), 4),
            }
        )
    from app.api.factor_lab.common import _round_metrics

    result = {
        "symbol": body.symbol,
        "timeframe": body.timeframe,
        "bars": n,
        "range": {
            "from": bars[0].get("time") if bars else None,
            "to": bars[-1].get("time") if bars else None,
        },
        "cost": cost,
        "cost_auto": body.cost is None,
        "metrics": _round_metrics(metrics),
        "live_metrics": _round_metrics(live_metrics),
        "equity_curve": curve,
    }
    if wf_detail is not None:
        result["walk_forward"] = {
            "folds": wf_detail["folds"],
            "wf_stable": wf_detail["wf_stable"],
            "wf_mean_test_sortino": round(wf_detail["wf_mean_test_sortino"], 4),
            "wf_mean_test_ann": round(wf_detail["wf_mean_test_ann"], 4),
            "wf_consistency": round(wf_detail["wf_consistency"], 4),
            "n_folds": wf_detail["n_folds"],
        }
    return result


@router.post("/compare")
async def api_compare(
    body: CompareRequest,
    request: Request,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """多个因子 vs 买入持有基线对比"""
    from app.services.factor_lab.scoring.ab_compare import compare as compare_factors
    from app.services.quota.quota_svc import (
        FEATURE_FACTOR,
        check_and_incr,
        get_effective_limits,
    )

    redis_conn = getattr(request.app.state, "redis", None)
    _limits = await get_effective_limits(session, _user)
    await check_and_incr(
        redis_conn, _user, FEATURE_FACTOR, _limits["factor_daily"], "因子实验室"
    )
    bars = await load_bars(
        redis_conn,
        session,
        body.symbol,
        body.timeframe,
        body.start_date,
        body.end_date,
    )
    reject_legacy_tokens(body.factor_tokens_list)
    bars = await _attach_aux(session, body.symbol, body.timeframe, bars, body.factor_tokens_list)
    cost = resolve_cost(body.symbol, bars, body.cost)
    result = await asyncio.to_thread(
        compare_factors, bars, body.timeframe, body.factor_tokens_list, cost
    )
    return {
        "symbol": body.symbol,
        "timeframe": body.timeframe,
        "range": {
            "from": bars[0].get("time") if bars else None,
            "to": bars[-1].get("time") if bars else None,
        },
        "cost": cost,
        "cost_auto": body.cost is None,
        **result,
    }
