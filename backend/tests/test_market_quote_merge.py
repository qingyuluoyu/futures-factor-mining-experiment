"""SimNow 主源 + 新浪逐字段兜底。"""

from app.data.quote_schema import filter_valid_quotes
from app.services.market_quote_merge import merge_live_quote, merge_live_quotes


def _simnow(**overrides):
    quote = {
        "symbol": "pp2609",
        "name": "聚丙烯2609",
        "exchange": "大商所",
        "last_price": 8500.0,
        "open_price": 8450.0,
        "high_price": 8600.0,
        "low_price": 8400.0,
        "pre_close": 8420.0,
        "bid_price": 8499.0,
        "ask_price": 8501.0,
        "bid_vol": 10,
        "ask_vol": 12,
        "volume": 1000,
        "position": 400000,
        "trade_date": "20260729",
        "recv_ts": 1000.0,
        "source": "simnow",
    }
    quote.update(overrides)
    return quote


def _sina(**overrides):
    quote = {
        "symbol": "pp2609",
        "name": "聚丙烯2609",
        "exchange": "大商所",
        "last_price": 8490.0,
        "open_price": 8440.0,
        "high_price": 8580.0,
        "low_price": 8390.0,
        "pre_close": 8410.0,
        "bid_price": 8489.0,
        "ask_price": 8491.0,
        "bid_vol": 8,
        "ask_vol": 9,
        "volume": 990,
        "position": 399000,
        "trade_date": "20260729",
        "source": "sina",
    }
    quote.update(overrides)
    return quote


def test_fresh_simnow_wins_field_by_field():
    merged = merge_live_quote(_simnow(), _sina(), now_ts=1005.0)

    assert merged is not None
    assert merged["last_price"] == 8500.0
    assert merged["open_price"] == 8450.0
    assert merged["source"] == "simnow"
    assert merged["field_meta"]["last_price"] == {
        "source": "simnow",
        "quality": "direct",
    }


def test_missing_simnow_field_falls_back_to_sina_only_for_that_field():
    merged = merge_live_quote(
        _simnow(open_price=None, high_price=1.7976931348623157e308),
        _sina(open_price=8440.0, high_price=8580.0),
        now_ts=1005.0,
    )

    assert merged is not None
    assert merged["last_price"] == 8500.0
    assert merged["open_price"] == 8440.0
    assert merged["high_price"] == 8580.0
    assert merged["field_meta"]["open_price"]["source"] == "sina"
    assert merged["field_meta"]["last_price"]["source"] == "simnow"


def test_stale_simnow_snapshot_falls_back_to_sina():
    merged = merge_live_quote(
        _simnow(recv_ts=800.0),
        _sina(),
        now_ts=1000.0,
        stale_after=90.0,
    )

    assert merged is not None
    assert merged["last_price"] == 8490.0
    assert merged["source"] == "sina"
    assert merged["simnow_stale"] is True


def test_older_simnow_trading_day_falls_back_to_sina():
    merged = merge_live_quote(
        _simnow(trade_date="20260728"),
        _sina(trade_date="20260729"),
        now_ts=1005.0,
    )

    assert merged is not None
    assert merged["last_price"] == 8490.0
    assert merged["field_meta"]["last_price"]["source"] == "sina"


def test_batch_merge_keeps_symbols_available_from_either_source():
    merged = merge_live_quotes(
        [_simnow()],
        [_sina(symbol="rb2610", name="螺纹钢2610")],
        now_ts=1005.0,
    )

    assert [q["symbol"] for q in merged] == ["pp2609", "rb2610"]
    assert merged[0]["source"] == "simnow"
    assert merged[1]["source"] == "sina"


# ---------- 三级兜底：simnow → sina → eastmoney ----------


def _eastmoney(**overrides):
    quote = {
        "symbol": "pp2609",
        "name": "聚丙烯2609",
        "exchange": "大商所",
        "last_price": 8485.0,
        "open_price": 8435.0,
        "high_price": 8590.0,
        "low_price": 8395.0,
        "pre_close": 8415.0,
        "bid_price": 8485.0,
        "ask_price": 8485.0,
        "bid_vol": 0,
        "ask_vol": 0,
        "volume": 950,
        "position": 390000,
    }
    quote.update(overrides)
    return quote


def test_eastmoney_ignored_after_removal():
    """东财兜底已移除：即使传入 eastmoney 也不再参与合并"""
    merged = merge_live_quote(
        _simnow(recv_ts=800.0),  # stale
        None,  # sina 无数据
        _eastmoney(),  # 已弃用参数
        now_ts=1000.0,
        stale_after=90.0,
    )
    # 无可用字段源 → 合并失败
    assert merged is None


def test_eastmoney_no_longer_fills_missing_field():
    """东财已移除：sina 缺的字段不再由东财补"""
    merged = merge_live_quote(
        _simnow(recv_ts=800.0),  # stale
        _sina(position=None),  # sina 缺 position
        _eastmoney(position=390000),  # 已弃用参数
        now_ts=1000.0,
        stale_after=90.0,
    )
    assert merged is not None
    assert merged["last_price"] == 8490.0  # sina 有效，优先
    assert not merged.get("position")  # sina 缺 → 不再由东财补
    assert merged["field_meta"]["position"]["source"] == "missing"


def test_sina_still_wins_over_eastmoney():
    """sina 有效字段优先于东财"""
    merged = merge_live_quote(
        _simnow(recv_ts=800.0),  # stale
        _sina(),
        _eastmoney(),
        now_ts=1000.0,
        stale_after=90.0,
    )
    assert merged is not None
    assert merged["last_price"] == 8490.0  # sina 优先
    assert merged["source"] == "sina"


def test_batch_merge_ignores_eastmoney_only_symbols():
    """东财已移除：仅东财有的合约不再出现在结果里"""
    merged = merge_live_quotes(
        [_simnow()],
        [_sina()],
        [_eastmoney(symbol="au2612", name="沪金2612")],  # 已弃用参数
        now_ts=1005.0,
    )
    symbols = [q["symbol"] for q in merged]
    assert "pp2609" in symbols
    assert "au2612" not in symbols


def test_eastmoney_none_backward_compatible():
    """eastmoney=None 时与原 2 源行为一致（向后兼容）"""
    merged = merge_live_quote(
        _simnow(),
        _sina(),
        None,  # 无东财
        now_ts=1005.0,
    )
    assert merged is not None
    assert merged["source"] == "simnow"


# ---------- VVTR 主源缺昨结：涨跌兜底新浪（PR：切 VVTR 后 +0.0% 修复） ----------


def test_vvtr_missing_pre_close_falls_back_to_sina_change():
    """VVTR 推送无 pre_close → 新浪昨结补位，涨跌按昨结重算"""
    vvtr = _simnow(
        source="vvtr",
        last_price=3100.0,
        pre_close=None,  # VVTR v1/v2 推送均无昨结字段
    )
    sina = _sina(pre_close=3050.0, last_price=3098.0)
    merged = merge_live_quote(vvtr, sina, None, now_ts=1005.0, primary_source="vvtr")
    assert merged is not None
    assert merged["pre_close"] == 3050.0
    assert merged["change"] == 3100.0 - 3050.0
    assert merged["change_pct"] == round((3100.0 - 3050.0) / 3050.0 * 100, 4)
    assert merged["source"] == "vvtr"  # 价格仍主源
    assert merged["field_meta"]["pre_close"]["source"] == "sina"


def test_premature_normalize_poisons_pre_close():
    """守卫:normalize 不得把缺失昨结填成 last(毒化 merge 兜底)

    历史:先 normalize 曾把 pre_close 填成 last,merge 无法再兜底,
    线上 +0.0%(2026-08-24 复发后根治)。现 normalize 对无基准 quote
    输出 pre_close=None / change=None,新浪(或 ws_bridge 昨收)补位
    依然生效。
    """
    vvtr_raw = _simnow(source="vvtr", pre_close=None)
    normalized = filter_valid_quotes([vvtr_raw])[0]
    assert normalized["pre_close"] is None  # 不再造 last
    assert normalized["change"] is None
    assert normalized["change_pct"] is None
    merged = merge_live_quote(normalized, _sina(pre_close=3050.0), None, now_ts=1005.0)
    assert merged["pre_close"] == 3050.0  # 新浪昨结补位恢复
    assert merged["change_pct"] != 0.0 or merged["last_price"] == merged["pre_close"]
