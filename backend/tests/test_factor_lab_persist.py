"""因子历史 FIFO / 教练 clamp / LLM 解析 单测"""

from __future__ import annotations

from app.services.factor_lab.llm.coach import clamp_coach_config
from app.services.factor_lab.llm.llm_parse import parse_candidates
from app.services.factor_lab.persist.tokens_codec import (
    normalize_tokens,
    tokens_key,
)
from app.services.factor_lab.search import SearchConfig, search
from app.services.factor_lab.vm import FEAT_OFFSET


def test_tokens_key_stable():
    assert tokens_key([1, 2, 3]) == "1,2,3"
    assert normalize_tokens(["1", 2, "x", 3.0]) == [1, 2, 3]


def test_clamp_coach_config_bounds():
    out = clamp_coach_config(
        {
            "population": 999,
            "generations": 1,
            "mutation_p": 9,
            "crossover_p": -1,
            "use_seed_tokens": 0,
            "note": "a" * 200,
        },
        30,
        15,
        0.3,
        0.6,
    )
    assert out["population"] == 100
    assert out["generations"] == 3
    assert out["mutation_p"] == 0.8
    assert out["crossover_p"] == 0.2
    assert out["use_seed_tokens"] is False
    assert len(out["note"]) <= 80


def test_clamp_coach_invalid_json_fallback():
    out = clamp_coach_config(None, 40, 12, 0.25, 0.55)
    assert out["population"] == 40
    assert out["generations"] == 12
    assert out["mutation_p"] == 0.25
    assert out["crossover_p"] == 0.55
    assert out["use_seed_tokens"] is True


def test_parse_candidates_filters_invalid():
    data = {
        "candidates": [
            {"tokens": [0, FEAT_OFFSET]},
            {"tokens": [-1, 0]},
            {"tokens": "bad"},
            {"tokens": [0] * 40},
            {"tokens": [0, FEAT_OFFSET]},
        ]
    }
    cands = parse_candidates(data)
    assert len(cands) == 1
    assert cands[0][0] == 0


def _make_bars(n: int = 80, seed: int = 1) -> list[dict]:
    import numpy as np

    rng = np.random.RandomState(seed)
    close = 100.0 + np.cumsum(rng.randn(n) * 0.4)
    bars: list[dict] = []
    for i, c in enumerate(close):
        bars.append(
            {
                "time": f"t{i}",
                "open": float(c),
                "high": float(c + 0.5),
                "low": float(c - 0.5),
                "close": float(c),
                "volume": 1000,
            }
        )
    return bars


def test_search_with_seed_tokens_returns_champions():
    bars = _make_bars(100)
    # 单特征 RET 作为种子
    cfg = SearchConfig(
        population=12,
        generations=3,
        top_n=5,
        seed=7,
        seed_tokens=[[0]],
    )
    champs = search(bars, "1d", cfg)
    assert isinstance(champs, list)
    assert len(champs) >= 1
    # 种子至少应出现在评估池中（综合分可能不是最高，但搜索应成功）
    assert all(isinstance(c.tokens, list) for c in champs)
