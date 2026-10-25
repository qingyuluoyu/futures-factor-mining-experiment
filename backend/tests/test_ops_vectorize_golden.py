"""ops 向量化语义等价 golden 测试

参照实现 = 改造前的逐 bar 循环（原样保留在测试里），
新向量化实现必须在随机数据上与其一致（rtol=1e-10）。
"""

from __future__ import annotations

import numpy as np

from app.services.factor_lab.ops import (
    delta,
    ts_corr,
    ts_max,
    ts_mean,
    ts_min,
    ts_rank,
    ts_std,
)


# ── 旧实现（逐 bar 循环，改造前原样） ──────────────────────


def _ref_mean(x, w):
    n = len(x)
    c = np.concatenate([[0.0], np.cumsum(x)])
    out = np.zeros(n)
    for i in range(n):
        lo = max(0, i - w + 1)
        out[i] = (c[i + 1] - c[lo]) / (i - lo + 1)
    return out


def _ref_reduce(x, w, fn):
    n = len(x)
    out = np.zeros(n)
    for i in range(n):
        lo = max(0, i - w + 1)
        out[i] = fn(x[lo : i + 1])
    return out


def _ref_rank(x, w):
    return _ref_reduce(
        x, w, lambda win: float((win <= win[-1]).sum()) / len(win)
    )


def _ref_corr(x, y, w):
    n = len(x)
    out = np.zeros(n)
    for i in range(n):
        lo = max(0, i - w + 1)
        a, b = x[lo : i + 1], y[lo : i + 1]
        if len(a) < 2:
            continue
        am, bm = a - a.mean(), b - b.mean()
        sa, sb = am.std(), bm.std()
        out[i] = 0.0 if (sa < 1e-9 or sb < 1e-9) else float((am * bm).mean() / (sa * sb))
    return out


# ── 对比 ────────────────────────────────────────────────────

RNG = np.random.RandomState(2024)
CASES = [
    RNG.randn(500) * 3.0,
    np.concatenate([np.full(50, 1.5), RNG.randn(200)]),  # 常数段→零方差分支
    RNG.randn(10),  # 短于窗口
    np.zeros(5),  # 全零
]
WINDOWS = [5, 10, 20, 60]


def test_ts_mean_equivalent():
    for x in CASES:
        for w in WINDOWS + [len(x) + 3]:
            np.testing.assert_allclose(
                ts_mean(x, w), _ref_mean(x, w), rtol=1e-10, atol=1e-12
            )


def test_ts_std_equivalent():
    for x in CASES:
        for w in WINDOWS:
            np.testing.assert_allclose(
                ts_std(x, w),
                np.sqrt(np.maximum(
                    _ref_mean(x, w) ** 2 * 0 + _ref_mean(x ** 2, w) - _ref_mean(x, w) ** 2, 0.0
                )),
                rtol=1e-10,
                atol=1e-12,
            )


def test_ts_max_min_equivalent():
    for x in CASES:
        for w in WINDOWS + [len(x) + 3]:
            np.testing.assert_allclose(
                ts_max(x, w), _ref_reduce(x, w, lambda s: s.max()), rtol=1e-10
            )
            np.testing.assert_allclose(
                ts_min(x, w), _ref_reduce(x, w, lambda s: s.min()), rtol=1e-10
            )


def test_ts_rank_equivalent():
    for x in CASES:
        for w in WINDOWS:
            np.testing.assert_allclose(
                ts_rank(x, w), _ref_rank(x, w), rtol=1e-10, atol=1e-12
            )


def test_ts_corr_equivalent():
    for x in CASES:
        y = RNG.randn(len(x))
        for w in (5, 20):
            np.testing.assert_allclose(
                ts_corr(x, y, w), _ref_corr(x, y, w), rtol=1e-10, atol=1e-12
            )


def test_delta_unchanged():
    x = RNG.randn(100)
    out = delta(x, 5)
    assert out[:5].tolist() == [0.0] * 5
    np.testing.assert_allclose(out[5:], x[5:] - x[:-5])


def test_vectorized_faster_than_reference():
    """sanity：向量化对长序列确有量级优势（防退化回循环实现）"""
    import time

    x = RNG.randn(20000)
    t0 = time.perf_counter()
    for _ in range(10):
        ts_rank(x, 20)
    t_new = time.perf_counter() - t0
    t0 = time.perf_counter()
    for _ in range(10):
        _ref_rank(x, 20)
    t_ref = time.perf_counter() - t0
    assert t_new < t_ref, f"向量化应快于参照循环: {t_new:.3f}s vs {t_ref:.3f}s"
