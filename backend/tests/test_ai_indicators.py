"""技术指标纯函数单测 —— 与前端算法关键行为对齐"""

from app.services.ai.tools.calc import (
    calc_boll,
    calc_ema,
    calc_kdj,
    calc_macd,
    calc_rsi,
    calc_sma,
    last_valid_points,
)


def test_calc_sma_basic() -> None:
    closes = [1.0, 2.0, 3.0, 4.0, 5.0]
    result = calc_sma(closes, 3)
    assert result[0] is None
    assert result[1] is None
    assert result[2] == 2.0
    assert result[3] == 3.0
    assert result[4] == 4.0


def test_calc_ema_seed_and_growth() -> None:
    closes = [10.0, 11.0, 12.0, 13.0, 14.0]
    result = calc_ema(closes, 3)
    assert result[0] is None
    assert result[1] is None
    assert result[2] is not None
    assert abs(result[2] - 11.0) < 1e-9
    assert result[3] is not None
    assert result[4] is not None
    assert result[4] > result[3]


def test_calc_macd_structure() -> None:
    closes = [float(i) for i in range(1, 60)]
    points = calc_macd(closes, 12, 26, 9)
    assert len(points) == len(closes)
    valid = [p for p in points if p["dif"] is not None]
    assert len(valid) > 0
    last = valid[-1]
    assert "dea" in last and "macd" in last


def test_calc_rsi_bounds() -> None:
    # 单调上涨 → RSI 偏高
    up = [float(i) for i in range(1, 40)]
    rsi_up = calc_rsi(up, 14)
    last_up = next(v for v in reversed(rsi_up) if v is not None)
    assert last_up > 70

    # 单调下跌 → RSI 偏低
    down = [float(i) for i in range(40, 0, -1)]
    rsi_down = calc_rsi(down, 14)
    last_down = next(v for v in reversed(rsi_down) if v is not None)
    assert last_down < 30


def test_calc_boll_bands_order() -> None:
    closes = [10.0, 11.0, 12.0, 11.5, 12.5, 13.0, 12.0, 11.0, 12.0, 13.0]
    points = calc_boll(closes, 5, 2.0)
    last = points[-1]
    assert last["upper"] is not None
    assert last["middle"] is not None
    assert last["lower"] is not None
    assert last["upper"] >= last["middle"] >= last["lower"]


def test_calc_kdj_has_values() -> None:
    highs = [11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 15.5, 16.5, 17.0, 16.0]
    lows = [9.0, 10.0, 11.0, 12.0, 13.0, 14.0, 13.5, 14.5, 15.0, 14.0]
    closes = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 14.5, 15.5, 16.0, 15.0]
    points = calc_kdj(highs, lows, closes, 5, 3, 3)
    last = points[-1]
    assert last["k"] is not None
    assert last["d"] is not None
    assert last["j"] is not None


def test_last_valid_points() -> None:
    times = ["t1", "t2", "t3", "t4"]
    values: list[float | None] = [None, 1.0, None, 2.0]
    points = last_valid_points(times, values, 2)
    assert points == [
        {"time": "t2", "value": 1.0},
        {"time": "t4", "value": 2.0},
    ]
