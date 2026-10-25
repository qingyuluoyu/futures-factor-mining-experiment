"""桌面端因子内核同步批次测试 —— 特征扩容 36→59 / aux 信息源 / .3 口径

覆盖(2026-09-28 批次,与桌面端 public/pykernel 同源):
- 特征清单:59 个(40 基础 + 19 扩展信息源定义),id 映射与文案覆盖
- 矩阵动态行数:无 x_* → 基础行;含扩展 token 的公式在基础矩阵上无信号
- feature_pool / bars_signature 的扩展语义
- aux_sources 纯函数:期限结构/板块动量/日内统计的手工小样本
- aux_attach:token 收集/组判定/挂载管道(mock 数据)/失败兜底
- 任务创建 token 值域校验(strict)
- P1-7 warmup 切片:段首指标与全量连续计算一致(核心性质)
"""

from __future__ import annotations

import numpy as np
import pytest

from app.services.factor_lab import FEATURE_NAMES, execute, to_text
from app.services.factor_lab.features import (
    BASE_FEAT_COUNT,
    clear_feature_matrix_cache,
    feature_matrix,
    feature_pool,
)
from app.services.factor_lab.features.aux_sources import (
    AUX_FEATURES,
    aggregate_daily,
    attach_sector,
    attach_term_structure,
    intraday_stats_by_day,
    sector_by_day,
    term_structure_by_day,
)
from app.services.factor_lab.vm import FEAT_OFFSET


def _bars(n: int = 300, seed: int = 5, minute: bool = False) -> list[dict]:
    rng = np.random.default_rng(seed)
    close = 1000.0 * np.cumprod(1.0 + rng.normal(0, 0.02, n))
    out = []
    for i, c in enumerate(close):
        t = (
            f"2026-{(i // 28) % 12 + 1:02d}-{i % 28 + 1:02d} 14:15:00"
            if minute
            else f"2026-{(i // 28) % 12 + 1:02d}-{i % 28 + 1:02d}"
        )
        out.append(
            {
                "time": t,
                "open": float(c * (1 + rng.normal(0, 0.002))),
                "high": float(c * 1.01),
                "low": float(c * 0.99),
                "close": float(c),
                "volume": float(1000 + rng.integers(0, 500)),
                "open_interest": float(5000 + i),
            }
        )
    return out


def _sec_bars(bars: list[dict]) -> list[dict]:
    out = [dict(b) for b in bars]
    for b in out:
        b.update(
            {
                "x_sec_ret5": 0.012,
                "x_sec_ret20": -0.008,
                "x_rel_ret20": 0.031,
                "x_sec_breadth": 0.25,
            }
        )
    return out


# ============ 特征清单与文案 ============


def test_feature_counts_and_ids():
    assert len(FEATURE_NAMES) == 59
    assert BASE_FEAT_COUNT == 40
    assert FEATURE_NAMES[35] == "STRENGTH"
    assert FEATURE_NAMES[36:40] == (
        "STREAK",
        "VWAP_DEV",
        "UPDOWN_VOL_RATIO",
        "CHAN_POS",
    )
    # aux 特征按组顺序:id 40 起 ts→sec→dtr→ids
    aux_names = tuple(n for n, _, _ in AUX_FEATURES)
    assert FEATURE_NAMES[40:] == aux_names
    assert FEATURE_NAMES[45] == "SEC_RET5"
    assert FEATURE_NAMES[58] == "ID_DAY_RET"


def test_desktop_features_computable():
    """36-39 桌面批次特征可执行且有区分度"""
    bars = _bars(300)
    clear_feature_matrix_cache()
    mat = feature_matrix(bars)
    for tid in (36, 37, 38, 39):
        row = mat[tid]
        assert np.isfinite(row).all()
        assert float(row.std()) > 1e-6, f"特征 {FEATURE_NAMES[tid]} 不应近常数"


# ============ 矩阵动态行数与抽样池 ============


def test_matrix_dynamic_rows_and_signal_gating():
    bars = _bars(300)
    clear_feature_matrix_cache()
    base = feature_matrix(bars)
    assert base.shape[0] == BASE_FEAT_COUNT

    sec = _sec_bars(bars)
    clear_feature_matrix_cache()
    full = feature_matrix(sec)
    assert full.shape[0] == len(FEATURE_NAMES)
    # 基础行逐位一致(挂载信息源不改既有特征)
    assert np.array_equal(base, full[:BASE_FEAT_COUNT])

    # sec 组 token(45)在基础矩阵上越界 → 无信号;挂载后可执行
    tokens = [45, FEAT_OFFSET + 27]  # LAG_1(SEC_RET5)
    assert execute(tokens, base) is None
    f = execute(tokens, full)
    assert f is not None and np.isfinite(f).all()


def test_feature_pool_scoping():
    bars = _bars(300)
    clear_feature_matrix_cache()
    base_mat = feature_matrix(bars)
    # 无扩展信息源 → int(原 rng 路径,与扩展前逐位一致)
    pool = feature_pool(bars, base_mat)
    assert isinstance(pool, int) and pool == BASE_FEAT_COUNT

    clear_feature_matrix_cache()
    sec_mat = feature_matrix(_sec_bars(bars))
    pool2 = feature_pool(_sec_bars(bars), sec_mat)
    assert isinstance(pool2, tuple)
    assert set(range(BASE_FEAT_COUNT)) <= set(pool2)
    # sec 组 id 45-48 入池;未启用的 ts/dtr/ids 组(40-44,49-58)不入池
    assert {45, 46, 47, 48} <= set(pool2)
    assert not {40, 44, 49, 58} & set(pool2)


def test_bars_signature_distinguishes_aux():
    bars = _bars(200)
    clear_feature_matrix_cache()
    feature_matrix(bars)
    feature_matrix(_sec_bars(bars))
    from app.services.factor_lab.features import _MATRIX_CACHE, bars_signature

    assert bars_signature(bars) in _MATRIX_CACHE
    assert bars_signature(_sec_bars(bars)) in _MATRIX_CACHE
    assert bars_signature(bars) != bars_signature(_sec_bars(bars))


# ============ aux_sources 纯函数(与桌面端同源) ============


def test_term_structure_by_day_manual():
    # 两合约:近月 3000、远月 3060,到期差 3 个月 → 年化 ln(3060/3000)/(0.25)
    curve = {
        "2026-01-15": [("rb2605", 3000.0, 80000.0), ("rb2609", 3060.0, 40000.0)],
        "2026-01-16": [("rb2605", 3000.0, 80000.0), ("rb2609", 3090.0, 40000.0)],
    }
    out = term_structure_by_day(curve)
    assert set(out) == {"2026-01-15", "2026-01-16"}
    d1, d2 = out["2026-01-15"], out["2026-01-16"]
    # 05→09 到期差 4 个月(同日 tau 偏移互相抵消) → 年化 ln(远/近)/(4/12)
    expect = float(np.log(3060.0 / 3000.0) / (4.0 / 12.0))
    assert abs(d1["x_ts_slope"] - expect) < 1e-9
    assert abs(d1["x_ts_main2"] - expect) < 1e-9  # 两点时主次价差=斜率
    # 斜率 5 日变化在第二个交易日为 0(不足 5 日按 0)
    assert d2["x_ts_slope_chg5"] == 0.0

    bars = [{"time": "2026-01-15", "close": 1.0}]
    attached = attach_term_structure(bars, out)
    assert attached[0]["x_ts_slope"] == pytest.approx(expect)


def test_sector_by_day_equal_weight():
    own = [
        {"time": f"2026-01-{d:02d}", "close": 100.0 + d} for d in range(1, 32)
    ]
    peers = {
        "hc": [
            {"time": f"2026-01-{d:02d}", "close": 50.0 + d * 2} for d in range(1, 32)
        ],
        "wr": [
            {"time": f"2026-01-{d:02d}", "close": 200.0 - d} for d in range(1, 32)
        ],
    }
    out = sector_by_day(own, peers)
    assert "2026-01-26" in out  # ≥20 日后 r20 有值
    d = out["2026-01-26"]
    hc_r20 = (50.0 + 2 * 26) / (50.0 + 2 * 6) - 1.0
    wr_r20 = (200.0 - 26) / (200.0 - 6) - 1.0
    own_r20 = (100.0 + 26) / (100.0 + 6) - 1.0
    assert abs(d["x_sec_ret20"] - (hc_r20 + wr_r20) / 2.0) < 1e-12
    assert abs(d["x_sec_ret5"] - ((50.0 + 2 * 26) / (50.0 + 2 * 21) - 1.0
                                  + (200.0 - 26) / (200.0 - 21) - 1.0) / 2.0) < 1e-12
    assert abs(d["x_rel_ret20"] - (own_r20 - (hc_r20 + wr_r20) / 2.0)) < 1e-12
    # 单调涨的伙伴在均线上方(+1)、单调跌的在下方(-1) → 广度 0
    assert d["x_sec_breadth"] == 0.0


def test_intraday_stats_and_aggregate_daily():
    minute = [
        # 交易日 1:夜盘 21:00 + 日盘两根
        {"time": "2026-01-05 21:00:00", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10.0},
        {"time": "2026-01-06 09:00:00", "open": 100.5, "high": 102.0, "low": 100.0, "close": 101.5, "volume": 20.0},
        {"time": "2026-01-06 15:00:00", "open": 101.5, "high": 103.0, "low": 101.0, "close": 102.5, "volume": 30.0},
    ]
    stats = intraday_stats_by_day(minute)
    # 夜盘归次一交易日:2026-01-06 含全部 3 根
    assert set(stats) == {"2026-01-06"}
    s = stats["2026-01-06"]
    # 夜盘段(21:00 一根):前收缺失 → 用首根开盘为基,夜盘收益=ln(100.5/100)
    assert abs(s["x_id_night_ret"] - float(np.log(100.5 / 100.0))) < 1e-9
    # 日盘段:夜盘末 → 收盘
    assert abs(s["x_id_day_ret"] - float(np.log(102.5 / 100.5))) < 1e-9
    # 无夜盘数据的次日:夜盘收益 0,日盘=全日
    stats2 = intraday_stats_by_day(minute[1:])
    assert stats2["2026-01-06"]["x_id_night_ret"] == 0.0
    assert abs(stats2["2026-01-06"]["x_id_day_ret"] - float(np.log(102.5 / 100.5))) < 1e-9

    agg = aggregate_daily(minute)
    assert [b["time"] for b in agg] == ["2026-01-06"]
    assert agg[0]["close"] == 102.5 and agg[0]["volume"] == 60.0


# ============ aux_attach 管道 ============


def test_collect_and_group_tokens():
    from app.services.factor_lab.aux_attach import (
        collect_factor_tokens,
        tokens_aux_groups,
        tokens_need_attach,
        tokens_unsupported_groups,
    )

    obj = {
        "strategy_params": {
            "factor_tokens": [[0, 45, 64], [46]],
            "decision_strategy": {"params": {"factor_tokens": [58]}},
        }
    }
    toks = collect_factor_tokens(obj)
    assert sorted(toks) == [0, 45, 46, 58, 64]
    assert tokens_aux_groups(toks) == {"sec", "ids"}
    assert tokens_need_attach(toks) is True
    assert tokens_unsupported_groups(toks) == {"ids"}
    # 纯基础 token 不触发挂载
    assert tokens_need_attach([0, 1, 64]) is False
    # 36-39 桌面批次(非 aux)不触发挂载——服务端已原生支持
    assert tokens_need_attach([36, 39]) is False


@pytest.mark.asyncio
async def test_attach_aux_for_tokens(monkeypatch):
    from app.services.factor_lab import aux_attach

    bars = _bars(300, seed=9)
    own = [
        {"time": f"2025-{(i // 28) % 12 + 1:02d}-{i % 28 + 1:02d}", "close": 100.0 + i}
        for i in range(360)
    ]
    peers = {
        "hc": [
            {"time": f"2025-{(i // 28) % 12 + 1:02d}-{i % 28 + 1:02d}", "close": 50.0 + i * 2}
            for i in range(360)
        ]
    }

    async def fake_daily(session, code, end, limit):
        return own if code.lower() == "rb" else peers.get(code.lower(), [])

    monkeypatch.setattr(aux_attach, "_cached_daily", fake_daily)
    from app.data.product_sectors import get_cross_peers

    monkeypatch.setattr(
        "app.data.product_sectors.get_cross_peers", lambda code: ["hc"]
    )

    out = await aux_attach.attach_aux_for_tokens(
        None, "rb2701", "1d", bars, [45, FEAT_OFFSET]
    )
    assert all("x_sec_ret5" in b for b in out)
    assert any(b.get("x_sec_ret5") is not None for b in out)
    # 入参 bars 未被改动(复制语义)
    assert "x_sec_ret5" not in bars[0]
    # 无需挂载的 token 原样返回同一对象
    same = await aux_attach.attach_aux_for_tokens(None, "rb2701", "1d", bars, [0])
    assert same is bars


@pytest.mark.asyncio
async def test_attach_aux_failure_falls_back(monkeypatch):
    from app.services.factor_lab import aux_attach

    async def boom(session, code, end, limit):
        raise RuntimeError("db down")

    monkeypatch.setattr(aux_attach, "_cached_daily", boom)
    bars = _bars(100)
    out = await aux_attach.attach_aux_for_tokens(None, "rb2701", "1d", bars, [45])
    assert out is bars  # 失败兜底:原样返回,公式退化为无信号


# ============ 任务创建 token 值域校验 ============


def test_factor_tokens_value_error():
    from fastapi import HTTPException

    from app.services.ai_trading.strategies.factor import (
        factor_tokens_value_error,
        normalize_factor_params,
    )

    assert factor_tokens_value_error([[0, 45, 64]]) is None
    assert factor_tokens_value_error([[36], [58]]) is None  # 定义域内(可算性另论)
    assert "旧版" in factor_tokens_value_error([[59]])
    assert "超出内核范围" in factor_tokens_value_error([[-1]])
    assert "超出内核范围" in factor_tokens_value_error([[104]])

    with pytest.raises(HTTPException) as ei:
        normalize_factor_params({"factor_tokens": [104]}, strict=True)
    assert ei.value.status_code == 400
    # 非 strict(运行时)保持宽容
    p = normalize_factor_params({"factor_tokens": [104]})
    assert p["factor_tokens"] == [104]


# ============ P1-7 warmup 切片:段首与连续计算一致 ============


def test_evaluate_on_slice_matches_continuous():
    from app.services.factor_lab.scoring.walk_forward import (
        evaluate_on_slice,
        split_bars,
    )

    bars = _bars(800, seed=13)
    tokens = [0, 1, FEAT_OFFSET + 1]  # SUB(RET, RET5)
    train, test = split_bars(bars, 0.7)
    lo = len(train)
    m_slice = evaluate_on_slice(tokens, bars, lo, len(bars), "1d", 0.0003)
    assert m_slice is not None
    for key in ("ann_ret", "sortino", "ts_ic"):
        assert np.isfinite(m_slice[key]), key

    # P1-7 核心性质:切片的因子序列与全量连续计算一致。两层验证:
    # (1) warmup 充分(> 特征窗 60 + 特征 zscore 窗 200 + vm 归一化窗 250
    #     = 510)时逐位一致——证明评估管道完全因果、无未来函数;
    # (2) WARMUP_BARS=250(与桌面端逐字同值)是效率取舍:vm 归一化窗内仍含
    #     轻微预热残留,切片与连续计算高度一致但不逐位(两端同款口径)。
    from app.services.factor_lab.scoring.walk_forward import WARMUP_BARS

    clear_feature_matrix_cache()
    f_slice_full = execute(tokens, feature_matrix(bars[lo - 550 : len(bars)]))[550:]
    clear_feature_matrix_cache()
    f_cont = execute(tokens, feature_matrix(bars))[lo:]
    # allclose:ts_mean 为 cumsum 实现,不同长度前缀有 ~1e-12 浮点累积差
    assert np.allclose(f_slice_full, f_cont, rtol=0.0, atol=1e-9)

    w = min(lo, WARMUP_BARS)
    clear_feature_matrix_cache()
    f_slice = execute(tokens, feature_matrix(bars[lo - w : len(bars)]))[w:]
    assert float(np.corrcoef(f_slice, f_cont)[0, 1]) > 0.99
    assert float(np.median(np.abs(f_slice - f_cont))) < 0.05


def test_norm_window_passthrough():
    """execute 的 norm_window 可由调用方传入(实盘/回测同参口径)"""
    bars = _bars(400, seed=21)
    clear_feature_matrix_cache()
    mat = feature_matrix(bars)
    f250 = execute([0], mat, norm_window=250)
    f100 = execute([0], mat, norm_window=100)
    assert f250 is not None and f100 is not None
    assert not np.array_equal(f250, f100)  # 窗口不同 → 数值不同
    # 因果不变量在自定义窗口下同样成立
    clear_feature_matrix_cache()
    cut = 320
    f_pre = execute([0], feature_matrix(bars[:cut]), norm_window=100)
    assert np.array_equal(f100[:cut], f_pre)
