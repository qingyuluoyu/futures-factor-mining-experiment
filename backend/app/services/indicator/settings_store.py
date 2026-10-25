"""图表指标配置读写 —— 纯函数，按用户 upsert，归一化兼容旧配置。

DEFAULT_CHART_CONFIG 对齐前端 types/indicator.ts 的 DEFAULT_INDICATOR_CONFIG（含 jdk/strength）。
_merge_dict 复用 services.ai.indicator_settings，避免重复实现。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.indicator_setting import ChartIndicatorSetting
from app.services.ai.indicator_settings import _merge_dict

# 完整默认指标配置 —— 与前端 DEFAULT_INDICATOR_CONFIG 完全对齐（含 jdk/strength）
DEFAULT_CHART_CONFIG: dict[str, Any] = {
    "maLines": [
        {"period": 5, "color": "#3b82f6"},
        {"period": 10, "color": "#f59e0b"},
        {"period": 20, "color": "#a855f7"},
    ],
    "macd": {
        "enabled": True,
        "fastPeriod": 12,
        "slowPeriod": 26,
        "signalPeriod": 9,
        "difColor": "#3b82f6",
        "deaColor": "#f59e0b",
        "histUpColor": "rgba(239,68,68,0.6)",
        "histDownColor": "rgba(34,197,94,0.6)",
    },
    "rsi": {
        "enabled": False,
        "period": 14,
        "overbought": 70,
        "oversold": 30,
        "lineColor": "#14b8a6",
        "overboughtColor": "rgba(239,68,68,0.7)",
        "oversoldColor": "rgba(34,197,94,0.7)",
    },
    "boll": {
        "enabled": False,
        "period": 20,
        "std": 2,
        "upperColor": "#a855f7",
        "middleColor": "#f59e0b",
        "lowerColor": "#a855f7",
        "upperStyle": "dashed",
        "middleStyle": "solid",
        "lowerStyle": "dashed",
    },
    "jdk": {
        "enabled": False,
        "rsvPeriod": 9,
        "kPeriod": 3,
        "dPeriod": 3,
        "kColor": "#f59e0b",
        "dColor": "#3b82f6",
        "jColor": "#ec4899",
    },
    # 强弱指标（0–100 归一化副图 + 三档进场；算法 services/signal_strength.py）
    "strength": {
        "enabled": False,
        "period": 14,
        "smooth": 3,
        "smooth2": 1,
        "trendMaPeriod": 10,
        "swingThreshold": 50.0,
        "reboundThreshold": 50.0,
        "oversoldLevel": 20.0,
        "reboundLookback": 10,
        "deepLevel": 5.0,
        "deepBars": 3,
        "cooldown": 8,
        "upColor": "#ef4444",
        "downColor": "#22d3ee",
        "midLineColor": "#a855f7",
        "signalColor": "#ffffff",
    },
    # 强弱 V2（形态档：衰竭/确认/中继，双向；算法 services/signal_strength_v2.py）
    "strengthV2": {
        "enabled": False,
        "period": 14,
        "smooth": 3,
        "smooth2": 2,
        "continuationBand": 5,
        "exhaustWindow": 4,
        "shrinkRatio": 0.45,
        "flatEps": 1.2,
        "zoneDrop": 10.0,
        "priceBufferAtrMult": 0.3,
        "atrPeriod": 14,
        "cooldown": 8,
        "upColor": "#ef4444",
        "downColor": "#22d3ee",
        "midLineColor": "#a855f7",
        "longSignalColor": "#ef4444",
        "shortSignalColor": "#22c55e",
    },
    # 强弱版本二选一；存量用户默认停留在 v1，行为不变
    "strengthVersion": "v1",
}

# 已下线功能的残留键：归一化时一律剥离，不回流前端/入库
_RETIRED_KEYS = ("pivot", "pivotV2", "pivotVersion")


def normalize_chart_config(raw: Any) -> dict[str, Any]:
    """归一化：以完整默认为底合并 raw，兼容缺字段的旧配置。"""
    if not isinstance(raw, dict):
        return deepcopy(DEFAULT_CHART_CONFIG)
    merged = _merge_dict(DEFAULT_CHART_CONFIG, raw)
    for key in _RETIRED_KEYS:
        merged.pop(key, None)
    return merged


async def get_settings(session: AsyncSession, user_id: UUID) -> dict[str, Any]:
    """读取用户指标配置；无记录返回默认（不入库）。"""
    row = await _fetch(session, user_id)
    if row is None:
        return deepcopy(DEFAULT_CHART_CONFIG)
    return normalize_chart_config(row.config)


async def has_settings(session: AsyncSession, user_id: UUID) -> bool:
    """用户是否已有指标配置记录（前端据此判断是否需迁移 localStorage）。"""
    return await _fetch(session, user_id) is not None


async def upsert_settings(
    session: AsyncSession,
    user_id: UUID,
    config: dict[str, Any],
) -> dict[str, Any]:
    """写入用户指标配置（有则更新、无则新建）；返回归一化后的配置。

    仅 add/update + flush，不 commit（由 get_session 依赖统一提交）。
    """
    normalized = normalize_chart_config(config)
    row = await _fetch(session, user_id)
    if row is None:
        row = ChartIndicatorSetting(user_id=user_id, config=normalized)
        session.add(row)
    else:
        row.config = normalized
    await session.flush()
    return deepcopy(normalized)


async def _fetch(
    session: AsyncSession, user_id: UUID
) -> ChartIndicatorSetting | None:
    """按 user_id 查询唯一配置行。"""
    result = await session.execute(
        select(ChartIndicatorSetting).where(
            ChartIndicatorSetting.user_id == user_id
        )
    )
    return result.scalar_one_or_none()
