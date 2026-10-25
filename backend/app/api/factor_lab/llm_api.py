"""路线 B：LLM 生成因子 API"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.factor_lab.common import (
    champion_payload,
    load_bars,
    reject_legacy_tokens,
    resolve_cost,
)
from app.core.deps import get_current_user, get_session
from app.models.user import User
from app.schemas.factor_lab import LlmGenerateRequest
from app.services.factor_lab.llm.llm_factor import generate_factors
from app.services.factor_lab.persist.history_svc import upsert_champions_fifo

logger = logging.getLogger("qihuo.factor_lab")
router = APIRouter()


@router.post("/llm-generate")
async def api_llm_generate(
    body: LlmGenerateRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """LLM 主导生成可执行因子（路线 B）"""
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
    cost = resolve_cost(body.symbol, bars, body.cost)
    try:
        result = await generate_factors(
            session=session,
            user_id=user.id,
            model_row_id=body.model_row_id,
            bars=bars,
            symbol=body.symbol,
            timeframe=body.timeframe,
            n_candidates=body.n_candidates,
            top_n=body.top_n,
            cost=cost,
            seed_tokens=body.seed_tokens,
            user_hint=body.user_hint,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("LLM 生成因子失败")
        raise HTTPException(status_code=500, detail=f"生成失败: {exc}") from exc

    champs = result["champions"]
    payload = champion_payload(champs, stamp_kernel=True)
    # 试验登记（多重检验修正原料，best-effort）
    from app.services.factor_lab.persist.trial_svc import log_search_trial

    await log_search_trial(
        session,
        user_id=user.id,
        source="llm_gen",
        symbol=body.symbol,
        timeframe=body.timeframe,
        trials=int(result.get("attempts") or 1) * body.n_candidates,
        bars=len(bars),
        config={
            "n_candidates": body.n_candidates,
            "top_n": body.top_n,
            "cost": cost,
            "start_date": body.start_date,
            "end_date": body.end_date,
        },
        champions=payload,
    )
    if payload:
        try:
            await upsert_champions_fifo(
                session,
                user.id,
                body.symbol,
                body.timeframe,
                payload,
                "llm_gen",
            )
            await session.commit()
        except Exception:
            logger.exception("LLM 生成写历史失败")
            await session.rollback()

    return {
        "symbol": body.symbol,
        "timeframe": body.timeframe,
        "bars": len(bars),
        "range": {
            "from": bars[0].get("time") if bars else None,
            "to": bars[-1].get("time") if bars else None,
        },
        "champions": payload,
        "portfolio": _portfolio_of(result["champions"], bars, body.timeframe, cost),
        "cost": cost,
        "cost_auto": body.cost is None,
        "llm_meta": {
            "attempts": result["attempts"],
            "dropped": result["dropped"],
            "source": "llm_gen",
        },
    }


def _portfolio_of(
    champs: list,
    bars: list,
    timeframe: str,
    cost: float,
) -> dict | None:
    strict = [c for c in champs if not (c.metrics or {}).get("overfit_warning")]
    if len(strict) < 2:
        return None
    from app.services.factor_lab.scoring.portfolio import evaluate_portfolio

    try:
        return evaluate_portfolio(
            bars, timeframe, [c.tokens for c in strict], cost
        )
    except Exception:
        logger.debug("LLM 组合评估失败（不影响生成结果）", exc_info=True)
        return None
