"""pg-tickdata 写入器单测 —— bar→row 转换（bob→eob）与交易所映射"""

from app.data.tickdata_writer import bar_to_row, resolve_exchange_code


def test_bar_to_row_minute_adds_period_for_eob():
    """分钟线 ts = bar 开始时间 + 1 周期（tickdata ts 为 eob）"""
    row = bar_to_row(
        "rb2610",
        "15m",
        {
            "time": "2026-08-20 10:45:00",
            "open": 3017, "high": 3020, "low": 3016, "close": 3019,
            "volume": 1234, "open_interest": 1904272,
        },
        "SHFE",
    )
    assert row is not None
    assert row[0] == 15           # period
    assert row[1] == "SHFE"       # exchange
    assert row[2] == "rb2610"     # instrument 小写
    assert str(row[3]) == "2026-08-20 11:00:00"  # eob = 10:45 + 15m
    assert row[4] == 3017 and row[8] == 1234
    assert row[9] is None         # amount
    assert row[10] == 1904272.0   # position


def test_bar_to_row_1m_and_daily():
    row1 = bar_to_row(
        "au2612", "1m",
        {"time": "2026-08-20 10:59:00", "open": 976, "high": 977,
         "low": 975, "close": 976, "volume": 10},
        "SHFE",
    )
    assert str(row1[3]) == "2026-08-20 11:00:00"
    rowd = bar_to_row(
        "rb2610", "1d",
        {"time": "2026-08-19", "open": 3016, "high": 3030,
         "low": 3010, "close": 3023, "volume": 749845},
        "SHFE",
    )
    assert rowd[0] == 1440
    assert str(rowd[3]) == "2026-08-19 00:00:00"  # 日线 ts 取日期


def test_bar_to_row_invalid_returns_none():
    assert bar_to_row("rb2610", "bad", {"time": "x"}) is None
    assert bar_to_row("rb2610", "15m", {"time": ""}) is None
    assert (
        bar_to_row("rb2610", "15m", {"time": "garbage"})
        is None
    )


def test_resolve_exchange_code():
    assert resolve_exchange_code("rb2610") == "SHFE"
    assert resolve_exchange_code("i2701") == "DCE"
    assert resolve_exchange_code("sr2701") == "CZCE"
    assert resolve_exchange_code("if2609") == "CFFEX"
    # 未知合约兜底 SHFE
    assert resolve_exchange_code("xx9999") == "SHFE"
