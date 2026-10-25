"""路线 B：LLM 生成可执行因子候选"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.factor_lab import (
    FEATURE_NAMES,
    OPS_NAMES,
    execute,
    is_constant,
    to_text,
    validate,
)
from app.services.factor_lab.features import feature_matrix
from app.services.factor_lab.llm.llm_http import chat_once, extract_json_object
from app.services.factor_lab.llm.llm_parse import parse_candidates
from app.services.factor_lab.scoring.evaluate import evaluate_factor
from app.services.factor_lab.scoring.periods import bars_per_year
from app.services.factor_lab.scoring.walk_forward import (
    MIN_TEST_BARS,
    split_bars,
)
from app.services.factor_lab.search import Champion, _dedup_top

logger = logging.getLogger("qihuo.factor_lab")

MAX_ROUNDS = 3
# 与 GA 搜索同款的防过拟合门控（train_ratio / WF 折数）
LLM_TRAIN_RATIO = 0.7
LLM_WF_FOLDS = 3


def _build_messages(
    symbol: str,
    timeframe: str,
    n_candidates: int,
    seed_tokens: list[list[int]] | None,
    user_hint: str | None,
    error_feedback: str | None,
    prev_champions: list[Champion] | None = None,
) -> list[dict[str, Any]]:
    feat_s = ", ".join(f"{i}:{n}" for i, n in enumerate(FEATURE_NAMES))
    # 算子 token = FEAT_OFFSET + idx；此处只给名称索引，要求模型用整数 token
    from app.services.factor_lab.vm import FEAT_OFFSET

    op_s = ", ".join(f"{FEAT_OFFSET + i}:{n}" for i, n in enumerate(OPS_NAMES))
    system = (
        "你是期货符号因子生成器。只输出 JSON："
        '{"candidates":[{"tokens":[int,...]}, ...]}\n'
        "tokens 为后缀表达式（StackVM）：先操作数（特征索引），后算子 token。\n"
        f"特征索引: {feat_s}\n"
        f"算子 token: {op_s}\n"
        "因子语义：输出经时序 zscore 标准化后按 tanh 映射为带符号仓位——"
        "正值=做多、负值=做空、接近 0=空仓；输出符号决定方向，纯幅度缩放"
        "会被标准化抵消。期货可双向交易，动量/反转/量仓类信号天然可正可负。\n"
        "偏好：输出围绕 0 波动（多空双向有区分度）的公式；避免恒正结构"
        "（TS_RANK/ABS 链会锁死单边，会被校验淘汰）。\n"
        "禁止 markdown 与其它文字。每个公式 1-12 个 token。"
    )
    user_parts = [
        f"品种={symbol} 周期={timeframe}",
        f"请生成 {n_candidates} 个不同候选因子。",
    ]
    if seed_tokens:
        seed_texts = [to_text(t) for t in seed_tokens[:3]]
        user_parts.append(f"可参考种子公式: {seed_texts}")
    if prev_champions:
        lines = []
        for c in prev_champions[:3]:
            m = c.metrics or {}
            lines.append(
                f"- {c.text}（综合{float(c.composite):.2f} "
                f"年化{float(m.get('ann_ret', 0)) * 100:.0f}% "
                f"换手{float(m.get('avg_turnover', 0)):.3f}）"
            )
        user_parts.append(
            "上一轮已验证可用的最优公式：\n" + "\n".join(lines)
            + "\n请生成与它们低相关（结构不同）的新候选，不要复述同构公式。"
        )
    if user_hint:
        user_parts.append(f"用户提示: {user_hint[:200]}")
    if error_feedback:
        user_parts.append(f"上一轮问题: {error_feedback}，请修正。")
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n".join(user_parts)},
    ]


def evaluate_token_candidates(
    bars: list[dict[str, Any]],
    timeframe: str,
    candidates: list[list[int]],
    cost: float,
    top_n: int,
) -> tuple[list[Champion], int]:
    """执行并评估候选：训练段评分 + 测试段/WF 门控（与 GA 同口径）

    此前直接在整段 bars 全样本评分晋级——LLM 路线与 GA 的淘汰标准
    完全不对称，等于每次取"全样本最优"。现改为：训练段（0.7）评分，
    _dedup_top 做测试段 sortino（1×/2×成本）+ walk-forward 3 折 +
    相关性去重，未通过者带 overfit_warning 回退。
    """
    train_bars, test_bars = split_bars(bars, LLM_TRAIN_RATIO)
    mat = feature_matrix(train_bars)
    close = np.array(
        [float(b.get("close") or 0) for b in train_bars], dtype=float
    )
    periods = bars_per_year(train_bars, timeframe)
    scored: list[tuple[float, list[int], dict]] = []
    dropped = 0
    for toks in candidates:
        bad = validate(toks)
        if bad:
            dropped += 1
            continue
        factor = execute(toks, mat)
        if factor is None or is_constant(factor):
            dropped += 1
            continue
        metrics = evaluate_factor(factor, close, cost=cost, periods=periods)
        scored.append((float(metrics["composite"]), list(toks), metrics))
    use_test = bool(test_bars) and len(test_bars) >= MIN_TEST_BARS
    champs = _dedup_top(
        scored,
        top_n,
        test_bars=test_bars,
        timeframe=timeframe,
        cost=cost,
        use_test=use_test,
        walk_forward_folds=LLM_WF_FOLDS,
        all_bars=bars,
        train_bars=train_bars,
        trials=len(candidates),
    )
    return champs, dropped


async def generate_factors(
    session: AsyncSession,
    user_id: UUID,
    model_row_id: UUID,
    bars: list[dict[str, Any]],
    symbol: str,
    timeframe: str,
    n_candidates: int,
    top_n: int,
    cost: float,
    seed_tokens: list[list[int]] | None,
    user_hint: str | None,
) -> dict[str, Any]:
    """多轮 LLM 生成 + VM 校验评估"""
    error_feedback: str | None = None
    attempts = 0
    last_dropped = 0
    champions: list[Champion] = []

    for round_i in range(MAX_ROUNDS):
        attempts = round_i + 1
        messages = _build_messages(
            symbol=symbol,
            timeframe=timeframe,
            n_candidates=n_candidates,
            seed_tokens=seed_tokens if round_i == 0 else None,
            user_hint=user_hint,
            error_feedback=error_feedback,
            prev_champions=champions if champions else None,
        )
        try:
            text = await chat_once(
                session=session,
                user_id=user_id,
                model_row_id=model_row_id,
                messages=messages,
                temperature=0.6,
                max_tokens=1024,
            )
        except Exception as exc:
            raise ValueError(str(exc)) from exc
        data = extract_json_object(text)
        cands = parse_candidates(data)
        if not cands:
            error_feedback = "未解析到合法 candidates.tokens"
            continue
        champions, last_dropped = evaluate_token_candidates(
            bars, timeframe, cands, cost, top_n
        )
        # 提前结束看严格筛冠军（不含兜底回退的 overfit_warning 因子）：
        # 若把兜底冠军也算数，第一轮 2 个回退因子就会触发 break，
        # 轮间反馈（上轮冠军喂回下一轮提示词）永远没有启动机会
        n_strict = sum(
            1 for c in champions if not (c.metrics or {}).get("overfit_warning")
        )
        if n_strict >= 2:
            break
        if n_strict >= 1 and round_i == MAX_ROUNDS - 1:
            break
        error_feedback = (
            f"通过样本外严格筛的因子仅 {n_strict} 个（其余测试段亏损或不稳健），"
            f"丢弃非法公式 {last_dropped} 个，请换结构更简单、信号更平滑的公式"
        )

    return {
        "champions": champions,
        "attempts": attempts,
        "dropped": last_dropped,
    }
