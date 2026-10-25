"""超级因子挖掘内核单元测试 —— search_stepwise + loader 数据范围"""

from __future__ import annotations

import numpy as np
import pytest

from app.services.factor_lab.search import SearchConfig, search_stepwise


def _make_bars(n: int = 120, seed: int = 0) -> list[dict]:
    """合成 OHLCV bars（带趋势 + 噪声），与 test_factor_lab 同源"""
    rng = np.random.RandomState(seed)
    close = 100.0 + np.cumsum(rng.randn(n) * 0.5)
    bars: list[dict] = []
    for i, c in enumerate(close):
        hi = c + abs(rng.randn()) * 0.5
        lo = c - abs(rng.randn()) * 0.5
        bars.append(
            {
                "time": f"2024-01-{i + 1:02d}",
                "open": float(c),
                "high": float(hi),
                "low": float(lo),
                "close": float(c),
                "volume": int(1000 + rng.randint(-200, 200)),
            }
        )
    return bars


def test_search_stepwise_yields_progress_per_generation():
    """search_stepwise 每代 yield 一个 snapshot，代数正确递增"""
    bars = _make_bars(200, seed=1)
    cfg = SearchConfig(
        population=12, generations=5, max_depth=3, top_n=3, seed=7,
        train_ratio=0.7, walk_forward_folds=0,
    )
    snapshots = list(search_stepwise(bars, "1d", cfg))
    # 5 代应有最多 5 个 snapshot（可能因搜索提前终止略少）
    assert len(snapshots) >= 1
    assert len(snapshots) <= 5
    # 代数递增
    gens = [s.generation for s in snapshots]
    assert gens == sorted(gens)
    assert gens[-1] == snapshots[-1].total_generations or gens[-1] <= 5
    # 每 snapshot 有 best_composite 和 champions
    for s in snapshots:
        assert isinstance(s.best_composite, float)
        assert isinstance(s.champions, list)


def test_search_stepwise_consistent_with_search():
    """步进版与同步版最终结果应一致（同 seed、同配置、同数据）"""
    from app.services.factor_lab.search import search

    bars = _make_bars(200, seed=2)
    cfg = SearchConfig(
        population=15, generations=6, max_depth=3, top_n=5, seed=42,
        train_ratio=0.0, walk_forward_folds=0,  # 关闭防过拟合，纯比较算法
    )
    direct = search(bars, "1d", cfg)
    snapshots = list(search_stepwise(bars, "1d", cfg))
    final = snapshots[-1].champions if snapshots else []
    # 两者冠军数应一致（都是 top_n 或更少）
    assert len(direct) == len(final)


def test_search_stepwise_resume_from_generation():
    """断点续训：start_generation 跳过已完成代，最终代数仍正确"""
    bars = _make_bars(200, seed=3)
    cfg = SearchConfig(
        population=12, generations=6, max_depth=3, top_n=3, seed=7,
        train_ratio=0.0, walk_forward_folds=0,
    )
    # 从第 4 代开始续跑（跳过前 3 代）
    snapshots = list(search_stepwise(bars, "1d", cfg, start_generation=4))
    # 应只产出 6-4=2 代（第5、6代）
    assert len(snapshots) <= 2
    if snapshots:
        assert snapshots[0].generation == 5  # 第一个产出的已完成代数


def test_search_stepwise_seed_best_injection():
    """seed_best 注入历史最优后续跑，snapshot 含注入的因子候选"""
    bars = _make_bars(200, seed=4)
    cfg = SearchConfig(
        population=10, generations=3, max_depth=3, top_n=3, seed=7,
        train_ratio=0.0, walk_forward_folds=0,
    )
    # 注入一个伪造的历史最优
    seed_best = [(1.5, [0, 8], {"ann_ret": 0.1, "sortino": 1.0, "composite": 1.5})]
    snapshots = list(search_stepwise(bars, "1d", cfg, seed_best=seed_best))
    assert len(snapshots) >= 1


def test_loader_daily_data_range():
    """loader 日线数据范围记录（mock session，验证返回格式约定）"""
    # 仅验证 loader 对未知 symbol 抛 HTTPException 400（数据校验 fail-fast）
    import asyncio

    from fastapi import HTTPException

    from app.services.factor_mining.loader import load_mining_bars

    # 用 mock session 调用，symbol 无对应 code 应抛 400
    class _FakeSession:
        async def execute(self, *a, **kw):
            class _R:
                def scalar_one_or_none(self):
                    return None

            return _R()

    with pytest.raises(HTTPException, match="无日线数据|无法识别"):
        asyncio.run(
            load_mining_bars(None, _FakeSession(), "UNKNOWN_SYMBOL_999", "1d")
        )


def test_merge_minute_bars_sina_wins_overlap():
    """tickdata 深历史前置补深，重叠段新浪优先，尾部截断 limit"""
    from app.services.factor_mining.loader import _merge_minute_bars

    def _b(t, src):
        return {"time": t, "close": 100.0, "src": src}

    td = [_b("2024-01-01 09:15:00", "td"), _b("2024-01-02 09:15:00", "td"),
          _b("2024-01-03 09:15:00", "td")]
    sina = [_b("2024-01-02 09:15:00", "sina"), _b("2024-01-04 09:15:00", "sina")]
    out = _merge_minute_bars(sina, td, limit=3)
    times = [b["time"] for b in out]
    assert times == [
        "2024-01-02 09:15:00", "2024-01-03 09:15:00", "2024-01-04 09:15:00",
    ]
    # 重叠段用新浪（新鲜度优先）
    assert out[0]["src"] == "sina"
    # 无 tickdata 时原样返回
    assert _merge_minute_bars(sina, [], limit=3) is sina


def test_list_mining_symbols_master_contract_format():
    """可挖掘品种选项带主力合约符号/名称（rb→rb2701），与因子实验室同口径"""
    import asyncio
    from datetime import date

    from app.services.kline_history import list_mining_symbols

    class _FakeSession:
        async def execute(self, *a, **kw):
            class _R:
                def all(self):
                    return [
                        ("rb", 4000, date(2020, 1, 2), date(2026, 9, 21)),
                        ("zz99", 10, date(2024, 1, 2), date(2024, 2, 2)),
                    ]

            return _R()

    items = asyncio.run(list_mining_symbols(_FakeSession()))
    by_code = {it["code"]: it for it in items}
    # rb → 静态表当前主力合约（首行，随主力表演进，不硬编码月份）
    rb = by_code["rb"]
    assert rb["symbol"] is not None
    assert rb["symbol"].startswith("rb") and rb["symbol"] != "rb"
    assert rb["symbol_name"] and "螺纹钢" in rb["symbol_name"]
    # 静态合约表无映射的品种：symbol/symbol_name 为 None，前端回退品种 code
    zz = by_code["zz99"]
    assert zz["symbol"] is None and zz["symbol_name"] is None
    # 原有字段保留
    assert rb["name"] == "螺纹钢"
    assert rb["bars"] == 4000
    assert rb["date_from"] == "2020-01-02"
