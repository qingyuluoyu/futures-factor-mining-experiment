"""因子实验室 —— meta / 搜索（含路线 A 教练）"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.factor_lab.common import (
    VALID_TF,
    champion_payload,
    load_bars,
    reject_legacy_tokens,
    resolve_cost,
)
from app.core.deps import get_current_user, get_session
from app.models.user import User
from app.schemas.factor_lab import SearchRequest
from app.services.factor_lab import FEATURE_NAMES, OPS_NAMES
from app.services.factor_lab.scoring.periods import bars_per_year
from app.services.factor_lab.persist.history_svc import upsert_champions_fifo
from app.services.factor_lab.search import SearchConfig, search

logger = logging.getLogger("qihuo.factor_lab")
router = APIRouter()


@router.get("/meta")
async def api_meta(_user: User = Depends(get_current_user)) -> dict:
    """特征/算子清单与默认参数

    kernel_version 供桌面端比对两端口径(特征/算子清单 + 版本戳),
    base_feat_count 为无扩展信息源时的特征矩阵行数。
    """
    from app.services.factor_lab import kernel_version
    from app.services.factor_lab.features import BASE_FEAT_COUNT

    return {
        "kernel_version": kernel_version(),
        "base_feat_count": BASE_FEAT_COUNT,
        "features": list(FEATURE_NAMES),
        "ops": list(OPS_NAMES),
        "timeframes": list(VALID_TF),
        "defaults": {
            "population": 30,
            "generations": 15,
            "top_n": 10,
            # null = 由后端按品种 tick + 手续费自动推导单边成本率
            "cost": None,
        },
    }


@router.post("/search")
async def api_search(
    body: SearchRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """遗传规划搜索；可选 LLM 教练；成功后写历史"""
    redis_conn = getattr(request.app.state, "redis", None)
    from app.services.quota.quota_svc import (
        FEATURE_FACTOR,
        check_and_incr,
        get_effective_limits,
    )

    _limits = await get_effective_limits(session, user)
    await check_and_incr(
        redis_conn, user, FEATURE_FACTOR, _limits["factor_daily"], "因子实验室"
    )
    bars = await load_bars(
        redis_conn,
        session,
        body.symbol,
        body.timeframe,
        body.start_date,
        body.end_date,
    )
    reject_legacy_tokens(body.seed_tokens or [])

    population = body.population
    generations = body.generations
    mutation_p = body.mutation_p if body.mutation_p is not None else 0.3
    crossover_p = body.crossover_p if body.crossover_p is not None else 0.6
    seed_tokens = body.seed_tokens
    coach_note: str | None = None
    source = "gp"

    if body.use_llm_coach:
        if body.model_row_id is None:
            raise HTTPException(status_code=400, detail="启用教练时请选择 AI 模型")
        from app.services.factor_lab.llm.coach import suggest_config

        coach = await suggest_config(
            session=session,
            user_id=user.id,
            model_row_id=body.model_row_id,
            symbol=body.symbol,
            timeframe=body.timeframe,
            bars_n=len(bars),
            base_population=population,
            base_generations=generations,
            base_mutation_p=mutation_p,
            base_crossover_p=crossover_p,
            has_seed=bool(seed_tokens),
        )
        population = int(coach["population"])
        generations = int(coach["generations"])
        mutation_p = float(coach["mutation_p"])
        crossover_p = float(coach["crossover_p"])
        coach_note = str(coach.get("note") or "") or None
        if not coach.get("use_seed_tokens", True):
            seed_tokens = None
        source = "evolve"

    if seed_tokens:
        source = "evolve" if source == "gp" and seed_tokens else source

    cost = resolve_cost(body.symbol, bars, body.cost)
    # 跨品种验证伙伴：异步预加载注入（热路径无 IO）；分钟线较重暂只日线
    cross_peers = None
    if body.cross_validate and body.timeframe == "1d":
        from app.services.factor_lab.cross_symbol import load_cross_peer_bars

        cross_peers = await load_cross_peer_bars(
            session, body.symbol, body.timeframe, max_bars=len(bars)
        )
    cfg = SearchConfig(
        population=population,
        generations=generations,
        top_n=body.top_n,
        seed=body.seed,
        cost=cost,
        mutation_p=mutation_p,
        crossover_p=crossover_p,
        seed_tokens=seed_tokens,
        train_ratio=body.train_ratio,
        test_recent_bars=body.test_recent_bars,
        walk_forward_folds=body.walk_forward_folds,
        live_fill_gate=body.live_fill_gate,
        cross_peers=cross_peers,
        islands=body.islands,
    )
    try:
        champs = await asyncio.to_thread(search, bars, body.timeframe, cfg)
    except Exception as exc:
        logger.exception("因子搜索失败")
        raise HTTPException(status_code=500, detail=f"搜索失败: {exc}") from exc

    payload = champion_payload(champs, stamp_kernel=True)
    # 试验登记（多重检验修正原料，best-effort）
    from app.services.factor_lab.persist.trial_svc import log_search_trial

    await log_search_trial(
        session,
        user_id=user.id,
        source=source,
        symbol=body.symbol,
        timeframe=body.timeframe,
        trials=population * generations + len(seed_tokens or []),
        bars=len(bars),
        config={
            "population": population,
            "generations": generations,
            "train_ratio": body.train_ratio,
            "test_recent_bars": body.test_recent_bars,
            "walk_forward_folds": body.walk_forward_folds,
            "live_fill_gate": body.live_fill_gate,
            "cross_validate": body.cross_validate,
            "cost": cost,
            "seed": body.seed,
            "start_date": body.start_date,
            "end_date": body.end_date,
        },
        champions=payload,
    )
    try:
        await upsert_champions_fifo(
            session,
            user.id,
            body.symbol,
            body.timeframe,
            payload,
            source,
        )
        await session.commit()
    except Exception:
        logger.exception("写入因子历史失败（不影响搜索结果）")
        await session.rollback()

    applied: dict[str, Any] = {
        "population": population,
        "generations": generations,
        "mutation_p": mutation_p,
        "crossover_p": crossover_p,
        "top_n": body.top_n,
        "seed": body.seed,
        "cost": cost,
        "cost_auto": body.cost is None,
        "periods": bars_per_year(bars, body.timeframe),
        "seed_count": len(seed_tokens or []),
        "source": source,
        "train_ratio": body.train_ratio,
        "test_recent_bars": body.test_recent_bars,
        "walk_forward_folds": body.walk_forward_folds,
        "live_fill_gate": body.live_fill_gate,
        "cross_validate": body.cross_validate,
        "cross_peers_loaded": [p for p, _ in (cross_peers or [])],
        "islands": body.islands,
    }
    return {
        "symbol": body.symbol,
        "timeframe": body.timeframe,
        "bars": len(bars),
        "range": {
            "from": bars[0].get("time") if bars else None,
            "to": bars[-1].get("time") if bars else None,
        },
        "champions": payload,
        "portfolio": _portfolio_of(champs, bars, body.timeframe, cost),
        "coach_note": coach_note,
        "applied_config": applied,
    }


def _portfolio_of(
    champs: list,
    bars: list[dict],
    timeframe: str,
    cost: float,
) -> dict | None:
    """冠军组合评估：仅用通过严格筛的因子（兜底回退的不参与组合）"""
    strict = [c for c in champs if not (c.metrics or {}).get("overfit_warning")]
    if len(strict) < 2:
        return None
    from app.services.factor_lab.scoring.portfolio import evaluate_portfolio

    try:
        return evaluate_portfolio(
            bars, timeframe, [c.tokens for c in strict], cost
        )
    except Exception:
        logger.debug("组合评估失败（不影响搜索结果）", exc_info=True)
        return None
