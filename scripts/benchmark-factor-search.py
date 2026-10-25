"""因子搜索基准评测 —— 固定 seed 合成数据，度量搜索质量与过拟合程度

用途：每次改动评分/搜索/表达空间后运行，对比 WF 通过率、OOS 中位数、
IS-OOS 落差是否改善。合成数据无真实 alpha，OOS 表现即噪声基线：
改动若让 champion 的 OOS 中位数下降或 IS-OOS 落差扩大，说明在放大过拟合。

用法：
  python scripts/benchmark-factor-search.py --out bench.json
  python scripts/benchmark-factor-search.py --compare bench_old.json bench_new.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import numpy as np

from app.services.factor_lab.search import SearchConfig, search


def make_bars(n: int, seed: int, trend_strength: float) -> list[dict]:
    """regime-switching 随机游走：趋势块 + 波动聚集（无真实 alpha）"""
    rng = np.random.RandomState(seed)
    rets = np.zeros(n)
    vol = 0.01
    i = 0
    while i < n:
        block = int(rng.randint(20, 120))
        direction = rng.choice([-1.0, 0.0, 1.0])
        for j in range(i, min(i + block, n)):
            vol = max(0.003, min(0.03, vol * 0.95 + abs(rng.randn()) * 0.002))
            rets[j] = direction * trend_strength * vol + rng.randn() * vol
        i += block
    close = 3000.0 * np.cumprod(1.0 + rets)
    bars = []
    d = 0
    for c in close:
        while d % 7 in (5, 6):  # 跳过周末，模拟交易日
            d += 1
        hi = c * (1 + abs(rng.randn()) * 0.003)
        lo = c * (1 - abs(rng.randn()) * 0.003)
        date = 7300 + d  # 2020-01-01 起的天数序号，仅用于 time 字段
        bars.append(
            {
                "time": f"2020-{((date // 28) % 12) + 1:02d}-{(date % 28) + 1:02d}",
                "open": float(c),
                "high": float(hi),
                "low": float(lo),
                "close": float(c),
                "volume": int(10000 + rng.randint(-3000, 3000)),
                "open_interest": int(200000 + rng.randint(-5000, 5000)),
            }
        )
        d += 1
    return bars


CASES = [
    # (名称, bar数, seed, 趋势强度)
    ("daily_trend", 1200, 11, 0.35),
    ("daily_choppy", 1200, 22, 0.05),
    ("min5_recent", 3000, 33, 0.20),
]


def run_benchmark() -> dict:
    results = {"cases": {}, "config": {
        "population": 30, "generations": 8, "train_ratio": 0.7,
        "walk_forward_folds": 3, "cost": 0.0005,
    }}
    for name, n_bars, seed, trend in CASES:
        bars = make_bars(n_bars, seed, trend)
        cfg = SearchConfig(
            population=30, generations=8, top_n=10, seed=42, cost=0.0005,
            train_ratio=0.7, walk_forward_folds=3,
        )
        t0 = time.time()
        champs = search(bars, "1d" if "daily" in name else "5m", cfg)
        wall = round(time.time() - t0, 2)
        train_anns, test_anns, wf_pass = [], [], 0
        for c in champs:
            m = c.metrics if isinstance(c.metrics, dict) else {}
            tm = m.get("train_metrics") or {}
            sm = m.get("test_metrics") or {}
            train_anns.append(float(tm.get("ann_ret") or m.get("ann_ret") or 0))
            test_anns.append(float(sm.get("ann_ret") or 0))
            if m.get("walk_forward", {}).get("wf_stable"):
                wf_pass += 1
        med = lambda xs: round(statistics.median(xs), 4) if xs else None
        results["cases"][name] = {
            "bars": n_bars,
            "champions": len(champs),
            "wf_pass": wf_pass,
            "wf_pass_rate": round(wf_pass / len(champs), 3) if champs else None,
            "train_ann_median": med(train_anns),
            "test_ann_median": med(test_anns),
            "is_oos_gap": (round(med(train_anns) - med(test_anns), 4)
                           if train_anns and test_anns else None),
            "wall_sec": wall,
        }
        print(f"{name}: {results['cases'][name]}")
    return results


def compare(old_path: str, new_path: str) -> None:
    old = json.loads(Path(old_path).read_text(encoding="utf-8"))
    new = json.loads(Path(new_path).read_text(encoding="utf-8"))
    print(f"{'case':<14} {'metric':<18} {'old':>10} {'new':>10} {'delta':>10}")
    for name in old["cases"]:
        if name not in new["cases"]:
            continue
        for k in ("wf_pass_rate", "train_ann_median", "test_ann_median",
                  "is_oos_gap", "wall_sec"):
            a, b = old["cases"][name].get(k), new["cases"][name].get(k)
            if a is None or b is None:
                continue
            d = round(b - a, 4)
            flag = ""
            if k in ("wf_pass_rate", "test_ann_median") and d < 0:
                flag = "  ← 恶化"
            if k == "is_oos_gap" and d > 0:
                flag = "  ← 恶化"
            print(f"{name:<14} {k:<18} {a:>10} {b:>10} {d:>+10}{flag}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="结果写入 JSON 文件")
    ap.add_argument("--compare", nargs=2, metavar=("OLD", "NEW"),
                    help="对比两份结果")
    args = ap.parse_args()
    if args.compare:
        compare(*args.compare)
        return
    res = run_benchmark()
    if args.out:
        Path(args.out).write_text(
            json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"written -> {args.out}")


if __name__ == "__main__":
    main()
