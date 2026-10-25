"""试验登记服务 —— 每次搜索写入 trials/champions 快照（best-effort）

多重检验修正的原料：不落库就永远无法回答"这个样本内分数里有多少
是 N 次尝试选出来的期望最大值"。失败只打日志，绝不影响搜索主流程。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.factor_lab import FactorSearchTrial

logger = logging.getLogger("qihuo.factor_lab")


def _slim_champions(champions: list[dict]) -> list[dict]:
    """champion_payload → 精简指标（够 PBO/DSR 用，不存大对象）"""
    out: list[dict] = []
    for c in champions or []:
        m = c.get("metrics") if isinstance(c, dict) else None
        m = m or {}
        tm = m.get("train_metrics") or {}
        sm = m.get("test_metrics") or {}
        wf = m.get("walk_forward") or {}
        out.append(
            {
                "tokens": list(c.get("tokens") or [])[:64],
                "composite": float(c.get("composite") or 0.0),
                "train_ann": float(tm.get("ann_ret") or m.get("ann_ret") or 0.0),
                "test_ann": float(sm.get("ann_ret") or 0.0),
                "wf_stable": bool(wf.get("wf_stable")),
                "overfit_warning": bool(c.get("overfit_warning")),
                # P5 统计严谨性字段
                "pbo_proxy": m.get("pbo_proxy"),
                "oos_conservative": m.get("oos_conservative"),
                "trials": m.get("trials"),
            }
        )
    return out


async def log_search_trial(
    session: AsyncSession,
    *,
    user_id: Any,
    source: str,
    symbol: str,
    timeframe: str,
    trials: int,
    bars: int,
    config: dict | None = None,
    champions: list[dict] | None = None,
) -> None:
    """写入一次搜索的试验登记；异常吞掉（登记失败不影响业务）"""
    try:
        session.add(
            FactorSearchTrial(
                user_id=user_id,
                source=str(source)[:16],
                symbol=str(symbol)[:20],
                timeframe=str(timeframe)[:8],
                trials=int(trials),
                bars=int(bars),
                config=config,
                champions=_slim_champions(champions or []),
            )
        )
        await session.flush()
    except Exception:
        logger.debug("试验登记写入失败（不影响主流程）", exc_info=True)
