"""tickdata 主力连续换月平滑（back-adjustment）单元测试

换月日 d0（dominant old→new），f = new(d0)/old(d0)，d0 及之前价格 ×f：
换月处的 close-to-close 收益应等于新主力自身收益。
"""

from __future__ import annotations

from datetime import date, datetime

from app.data.tickdata_kline import (
    _apply_backward_adjustment,
    detect_rolls,
)


def _rec(ts: str, instrument: str, volume: int, close: float) -> dict:
    return {"ts": datetime.fromisoformat(ts), "instrument": instrument,
            "volume": volume, "close": close}


def _bar(t: str, close: float) -> dict:
    return {"time": t, "open": close, "high": close, "low": close,
            "close": close, "volume": 1}


# ---------- detect_rolls ----------

def test_detect_rolls_basic_switch():
    """d1 dominant 从 rb2110 切到 rb2111：f = rb2111(d0)/rb2110(d0)"""
    rows = [
        _rec("2024-01-01", "rb2110", 100, 4000.0),
        _rec("2024-01-01", "rb2111", 10, 4020.0),
        _rec("2024-01-02", "rb2110", 100, 4010.0),  # d0：老主力仍是 dominant
        _rec("2024-01-02", "rb2111", 50, 4030.0),
        _rec("2024-01-03", "rb2111", 100, 4040.0),  # d1：新主力成为 dominant
        _rec("2024-01-03", "rb2110", 10, 4015.0),
    ]
    rolls = detect_rolls(rows)
    assert len(rolls) == 1
    d0, f = rolls[0]
    assert d0 == "2024-01-02"
    assert abs(f - 4030.0 / 4010.0) < 1e-12  # new(d0)/old(d0)


def test_detect_rolls_no_switch():
    rows = [
        _rec("2024-01-01", "rb2110", 100, 4000.0),
        _rec("2024-01-02", "rb2110", 100, 4010.0),
    ]
    assert detect_rolls(rows) == []


# ---------- _apply_backward_adjustment ----------

def test_adjustment_removes_roll_jump():
    """换月处拼接收益 → 新主力自身收益

    构造：d0 收 4000（老合约），d1 收 4030（新合约，自身前收 4020）。
    未平滑收益 = 0.75%；新主力自身收益 = 10/4020 ≈ 0.249%。
    平滑后 d0' = 4000 × (4020/4000) = 4020，收益 = 4030/4020-1 ✓
    """
    bars = [_bar("2024-01-01", 4000.0), _bar("2024-01-02", 4030.0)]
    rolls = [("2024-01-01", 4020.0 / 4000.0)]  # f = new(d0)/old(d0)
    out = _apply_backward_adjustment(bars, rolls)
    assert abs(out[0]["close"] - 4020.0) < 1e-9   # d0 重述为新合约口径
    assert out[1]["close"] == 4030.0              # 换月日之后不动
    adj_ret = out[1]["close"] / out[0]["close"] - 1
    true_ret = 4030.0 / 4020.0 - 1
    assert abs(adj_ret - true_ret) < 1e-12


def test_adjustment_chains_multiple_rolls():
    """两次换月链式相乘：d0 当天（旧主力最后一日）也被其后的换月调整"""
    bars = [
        _bar("2024-01-01", 100.0),
        _bar("2024-01-02", 101.0),   # roll1 的 d0（老主力最后一天）
        _bar("2024-01-03", 102.0),   # roll2 的 d0
        _bar("2024-01-04", 103.0),
    ]
    f1, f2 = 1.01, 0.99
    out = _apply_backward_adjustment(bars, [("2024-01-02", f1), ("2024-01-03", f2)])
    assert abs(out[0]["close"] - 100.0 * f1 * f2) < 1e-9
    assert abs(out[1]["close"] - 101.0 * f1 * f2) < 1e-9
    assert abs(out[2]["close"] - 102.0 * f2) < 1e-9
    assert out[3]["close"] == 103.0


def test_adjustment_empty_rolls_noop():
    bars = [_bar("2024-01-01", 4000.0)]
    out = _apply_backward_adjustment(bars, [])
    assert out is bars


def test_adjustment_ohlc_all_scaled():
    b = {"time": "2024-01-01", "open": 1.0, "high": 2.0, "low": 0.5,
         "close": 1.5, "volume": 7}
    out = _apply_backward_adjustment([b], [("2024-01-02", 2.0)])
    assert out[0]["open"] == 2.0 and out[0]["high"] == 4.0
    assert out[0]["low"] == 1.0 and out[0]["close"] == 3.0
    assert out[0]["volume"] == 7  # 量/仓不变
