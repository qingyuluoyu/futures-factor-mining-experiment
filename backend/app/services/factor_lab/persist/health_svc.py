"""收藏因子健康度刷新 —— 衰减监控服务层（每日一次，IC 口径）"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.factor_lab import FactorFavorite
from app.services.factor_lab.scoring.decay import rolling_health

logger = logging.getLogger("qihuo.factor_health")


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def refresh_favorites_health(
    session: AsyncSession,
    load_bars,
    bars_limit: int = 400,
) -> dict[str, int]:
    """刷新全部收藏因子的 health 字段（衰减判定 = 250 根滚动 IC ≤ 0）

    load_bars: async (symbol, timeframe, limit) -> bars（由调用方注入）。
    按 (symbol, timeframe) 分组共享一次数据加载。
    返回 {checked, decayed, skipped, groups}。
    """
    rows = list(
        (await session.execute(select(FactorFavorite))).scalars().all()
    )
    groups: dict[tuple[str, str], list[FactorFavorite]] = {}
    for r in rows:
        sym = (r.symbol or "").strip().lower()
        tf = (r.timeframe or "").strip()
        if sym and tf:
            groups.setdefault((sym, tf), []).append(r)
    checked = decayed = 0
    for (sym, tf), favs in groups.items():
        try:
            bars = await load_bars(sym, tf, bars_limit)
        except Exception:
            logger.debug("健康刷新取数失败 %s %s", sym, tf, exc_info=True)
            bars = []
        if not bars:
            continue
        for fav in favs:
            health = rolling_health(list(fav.tokens or []), bars, tf)
            if health is None:
                continue
            health["checked_at"] = _now().isoformat()
            fav.health = health
            checked += 1
            decayed += 1 if health["decay"] else 0
    if checked:
        await session.commit()
    logger.info(
        "因子健康刷新：组=%d 检查=%d 衰减=%d",
        len(groups), checked, decayed,
    )
    return {
        "checked": checked,
        "decayed": decayed,
        "skipped": len(rows) - checked,
        "groups": len(groups),
    }
