"""K 线 API 字段扩展测试

验证 `/api/market/kline` 返回的每条 bar 包含 8 个字段：
time, open, high, low, close, volume, settle, open_interest
并验证旧版缓存数据缺字段时被正确补全为 None。
"""

from app.services.kline import _normalize_bar, _normalize_bars


EXPECTED_FIELDS: set[str] = {
    "time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "settle",
    "open_interest",
}


def test_normalize_bar_adds_missing_fields():
    """旧版 bar（只有 6 字段）补全为 8 字段，settle/open_interest 为 None"""
    legacy = {
        "time": "2026-07-01",
        "open": 3600.0,
        "high": 3650.0,
        "low": 3580.0,
        "close": 3620.0,
        "volume": 123456,
    }
    result = _normalize_bar(legacy)
    assert set(result.keys()) == EXPECTED_FIELDS
    assert result["settle"] is None
    assert result["open_interest"] is None


def test_normalize_bar_preserves_existing_settle_and_open_interest():
    """已有 settle/open_interest 的 bar 不被覆盖"""
    full = {
        "time": "2026-07-01",
        "open": 3600.0,
        "high": 3650.0,
        "low": 3580.0,
        "close": 3620.0,
        "volume": 123456,
        "settle": 3610.0,
        "open_interest": 98765,
    }
    result = _normalize_bar(full)
    assert result["settle"] == 3610.0
    assert result["open_interest"] == 98765


def test_normalize_bars_handles_empty_list():
    """空数组安全处理"""
    assert _normalize_bars([]) == []


def test_normalize_bars_processes_mixed():
    """混合旧版/新版 bar 的批量补全"""
    bars = [
        {
            "time": "2026-07-01",
            "open": 3600.0,
            "high": 3650.0,
            "low": 3580.0,
            "close": 3620.0,
            "volume": 100,
        },
        {
            "time": "2026-07-02",
            "open": 3620.0,
            "high": 3680.0,
            "low": 3610.0,
            "close": 3670.0,
            "volume": 200,
            "settle": 3650.0,
            "open_interest": 50000,
        },
    ]
    result = _normalize_bars(bars)
    assert len(result) == 2
    for bar in result:
        assert set(bar.keys()) == EXPECTED_FIELDS
    assert result[0]["settle"] is None
    assert result[0]["open_interest"] is None
    assert result[1]["settle"] == 3650.0
    assert result[1]["open_interest"] == 50000


def test_kline_bar_schema_accepts_optional_fields():
    """Pydantic schema 接受 6 字段（旧客户端）和 8 字段（新客户端）"""
    from app.schemas.kline import KlineBarSchema

    legacy = KlineBarSchema(
        time="2026-07-01",
        open=3600.0,
        high=3650.0,
        low=3580.0,
        close=3620.0,
        volume=100,
    )
    assert legacy.settle is None
    assert legacy.open_interest is None

    full = KlineBarSchema(
        time="2026-07-01",
        open=3600.0,
        high=3650.0,
        low=3580.0,
        close=3620.0,
        volume=100,
        settle=3610.0,
        open_interest=98765,
    )
    assert full.settle == 3610.0
    assert full.open_interest == 98765


def test_sina_normalize_record_extracts_settle_and_open_interest_for_daily():
    """日线 sina record 包含 s(结算价) 和 p(持仓量)，应被正确提取"""
    from app.data.sina_kline import _normalize_record

    sina_daily = {
        "d": "2026-06-30",
        "o": "3095.000",
        "h": "3095.000",
        "l": "3076.000",
        "c": "3088.000",
        "v": "541315",
        "p": "1971770",  # 持仓量
        "s": "3084.000",  # 结算价
    }
    bar = _normalize_record(sina_daily)
    assert bar["settle"] == 3084.0
    assert bar["open_interest"] == 1971770
    assert bar["volume"] == 541315


def test_sina_normalize_record_handles_minute_bar_without_settle():
    """分钟线 sina record 只有 p(持仓量)，没有 s(结算价)，settle 应为 None"""
    from app.data.sina_kline import _normalize_record

    sina_minute = {
        "d": "2026-07-01 11:30:00",
        "o": "3066.000",
        "h": "3068.000",
        "l": "3066.000",
        "c": "3066.000",
        "v": "1743",
        "p": "2032218",
    }
    bar = _normalize_record(sina_minute)
    assert bar["settle"] is None
    assert bar["open_interest"] == 2032218


def test_sina_normalize_record_handles_missing_position_field():
    """sina record 完全缺 p 字段时，open_interest 应为 None 而非 0"""
    from app.data.sina_kline import _normalize_record

    sina_no_position = {
        "d": "2026-07-01",
        "o": "3000.000",
        "h": "3010.000",
        "l": "2990.000",
        "c": "3005.000",
        "v": "100",
    }
    bar = _normalize_record(sina_no_position)
    assert bar["open_interest"] is None
    assert bar["settle"] is None
