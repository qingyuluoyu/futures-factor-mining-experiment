"""路线 A：LLM 教练 —— 只建议 SearchConfig，不写 token"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.factor_lab.llm.coach_prompt import build_coach_messages
from app.services.factor_lab.llm.llm_http import chat_once, extract_json_object

logger = logging.getLogger("qihuo.factor_lab")


def clamp_coach_config(
    raw: dict[str, Any] | None,
    base_population: int,
    base_generations: int,
    base_mutation_p: float,
    base_crossover_p: float,
) -> dict[str, Any]:
    """将教练输出 clamp 到合法区间；非法则回退基准"""
    src = raw if isinstance(raw, dict) else {}

    def _int(key: str, lo: int, hi: int, fallback: int) -> int:
        try:
            v = int(src.get(key, fallback))
        except (TypeError, ValueError):
            return fallback
        return max(lo, min(hi, v))

    def _float(key: str, lo: float, hi: float, fallback: float) -> float:
        try:
            v = float(src.get(key, fallback))
        except (TypeError, ValueError):
            return fallback
        return max(lo, min(hi, v))

    use_seed = src.get("use_seed_tokens", True)
    if not isinstance(use_seed, bool):
        use_seed = bool(use_seed)
    note = str(src.get("note") or "").strip()[:80]
    return {
        "population": _int("population", 10, 100, base_population),
        "generations": _int("generations", 3, 50, base_generations),
        "mutation_p": _float("mutation_p", 0.05, 0.8, base_mutation_p),
        "crossover_p": _float("crossover_p", 0.2, 0.9, base_crossover_p),
        "use_seed_tokens": use_seed,
        "note": note,
    }


async def suggest_config(
    session: AsyncSession,
    user_id: UUID,
    model_row_id: UUID,
    symbol: str,
    timeframe: str,
    bars_n: int,
    base_population: int,
    base_generations: int,
    base_mutation_p: float,
    base_crossover_p: float,
    has_seed: bool,
) -> dict[str, Any]:
    """调用 LLM 教练；失败时静默回退基准参数"""
    fallback = clamp_coach_config(
        None,
        base_population,
        base_generations,
        base_mutation_p,
        base_crossover_p,
    )
    messages = build_coach_messages(
        symbol=symbol,
        timeframe=timeframe,
        bars_n=bars_n,
        base_population=base_population,
        base_generations=base_generations,
        base_mutation_p=base_mutation_p,
        base_crossover_p=base_crossover_p,
        has_seed=has_seed,
    )
    try:
        text = await chat_once(
            session=session,
            user_id=user_id,
            model_row_id=model_row_id,
            messages=messages,
            temperature=0.3,
            max_tokens=256,
        )
        data = extract_json_object(text)
        return clamp_coach_config(
            data,
            base_population,
            base_generations,
            base_mutation_p,
            base_crossover_p,
        )
    except Exception as exc:
        logger.warning("因子教练失败，回退默认: %s", exc)
        fallback["note"] = f"教练不可用，已用默认参数（{exc}）"[:80]
        return fallback
