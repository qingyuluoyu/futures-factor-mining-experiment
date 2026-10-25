"""VVTR 行情映射单测 —— WS v2 type=14 推送 / WS v1 L1 tick / HTTP snapshot → 统一 quote"""

from app.connectors.vvtr.config import is_master_symbol
from app.connectors.vvtr.quote_map import (
    compute_trade_date,
    vvtr_snapshot_to_quote,
    vvtr_v1_message_to_quote,
    vvtr_ws_message_to_quote,
)


def _ws_msg(**overrides):
    msg = {
        "t": "20251219220600250",
        "s": "sr2609",
        "p": 2153,
        "v": 3,
        "total_vol": 305581,
        "open": 2141,
        "high": 2158,
        "low": 2131,
        "pre_close": 2148,
        "pre_settle": 2160,
        "price_chg": -7,
        "price_chg_radio": -0.324074,
        "open_int": 862363,
        "turnover": 6554801410,
        "avg_price": 2145,
        "low_limit": 2030,
        "upper_limit": 2290,
        "q": [
            {"bP": 2152, "bV": 93, "aP": 2153, "aV": 186},
            {"bP": 2151, "bV": 158, "aP": 2154, "aV": 249},
            {"bP": 2150, "bV": 158, "aP": 2155, "aV": 361},
            {"bP": 2149, "bV": 271, "aP": 2156, "aV": 167},
            {"bP": 2148, "bV": 237, "aP": 2157, "aV": 144},
        ],
    }
    msg.update(overrides)
    return msg


def test_ws_message_maps_core_fields():
    quote = vvtr_ws_message_to_quote(_ws_msg())
    assert quote is not None
    assert quote["symbol"] == "sr2609"
    assert quote["last_price"] == 2153
    assert quote["open_price"] == 2141
    assert quote["high_price"] == 2158
    assert quote["low_price"] == 2131
    # 期货涨跌幅基准=昨结算(pre_settle=2160),非帧内昨收(2148)——
    # 旧映射把昨收装进 pre_close,merge 链重算百分比变成股票口径
    assert quote["pre_close"] == 2160
    assert quote["settlement_price"] == 2160
    assert quote["volume"] == 305581
    assert quote["position"] == 862363
    assert quote["turnover"] == 6554801410
    assert quote["avg_price"] == 2145
    assert quote["upper_limit_price"] == 2290
    assert quote["lower_limit_price"] == 2030
    # 源端 change/pct 口径不可考：置空，由 normalize 用昨结基准统一重算
    assert quote["change"] is None
    assert quote["change_pct"] is None
    assert quote["source"] == "vvtr"


def test_ws_message_pre_close_falls_back_to_yesterday_close():
    """帧缺 pre_settle 时回退昨收（近似基准好过无基准）"""
    quote = vvtr_ws_message_to_quote(_ws_msg(pre_settle=None))
    assert quote is not None
    assert quote["pre_close"] == 2148
    assert quote["settlement_price"] is None


def test_ws_message_pct_recomputed_on_prev_settle():
    """端到端：normalize 用 昨结 基准重算涨跌幅（期货口径）"""
    from app.data.quote_schema import normalize_quote_fields

    quote = vvtr_ws_message_to_quote(_ws_msg())
    assert quote is not None
    norm = normalize_quote_fields(quote)
    assert norm is not None
    assert norm["pre_close"] == 2160
    # (2153 - 2160) / 2160 = -0.3241%
    assert round(norm["change"], 4) == -7.0
    assert round(norm["change_pct"], 4) == round((2153 - 2160) / 2160 * 100, 4)


def test_ws_message_maps_level1_and_5_depth():
    quote = vvtr_ws_message_to_quote(_ws_msg())
    assert quote["bid_price"] == 2152
    assert quote["bid_vol"] == 93
    assert quote["ask_price"] == 2153
    assert quote["ask_vol"] == 186
    assert len(quote["bid_levels"]) == 5
    assert len(quote["ask_levels"]) == 5
    assert quote["bid_levels"][4] == {"price": 2148, "volume": 237}
    assert quote["ask_levels"][4] == {"price": 2157, "volume": 144}


def test_ws_message_drops_master_symbols():
    assert vvtr_ws_message_to_quote(_ws_msg(s="rb9999")) is None
    assert vvtr_ws_message_to_quote(_ws_msg(s="rb9998")) is None
    assert vvtr_ws_message_to_quote(_ws_msg(s="rb8888")) is None
    assert vvtr_ws_message_to_quote(_ws_msg(s="rb9999x")) is not None  # 非合成后缀


def test_ws_message_invalid_price_returns_none():
    assert vvtr_ws_message_to_quote(_ws_msg(p=0)) is None
    assert vvtr_ws_message_to_quote(_ws_msg(p=None)) is None


def test_ws_message_missing_levels_kept_empty():
    quote = vvtr_ws_message_to_quote(_ws_msg(q=[{"bP": 0, "bV": 0, "aP": 0, "aV": 0}]))
    assert quote is not None
    assert quote["bid_levels"] == []
    assert quote["ask_levels"] == []


def test_trade_date_day_session_is_same_day():
    # 日盘 10:15 → 当天
    assert compute_trade_date("20251219101500000") == "2025-12-19"
    assert compute_trade_date("2025-12-19 10:15:00") == "2025-12-19"


def test_trade_date_night_session_rolls_to_next_weekday():
    # 周五 22:06 夜盘 → 下周一
    assert compute_trade_date("20251219220600250") == "2025-12-22"
    # 周三 21:00 夜盘 → 周四
    assert compute_trade_date("20251217210000000") == "2025-12-18"
    # 周六凌晨 01:30（周五夜盘尾段）→ 下周一
    assert compute_trade_date("20251220013000000") == "2025-12-22"


def test_trade_date_invalid_returns_empty():
    assert compute_trade_date("") == ""
    assert compute_trade_date("garbage") == ""


def test_snapshot_maps_l1_fields():
    snap = {
        "s": "rb2610",
        "o": 3026,
        "h": 3042,
        "l": 3017,
        "p": 3037,
        "cV": 884632,
        "cA": 26794549120,
        "cP": 2020387,
        "tt": 5,
        "lV": 40,
        "lA": 1214730,
        "q": [{"aP": 3037, "aV": 79, "bP": 3036, "bV": 2632}],
    }
    quote = vvtr_snapshot_to_quote(snap)
    assert quote is not None
    assert quote["symbol"] == "rb2610"
    assert quote["last_price"] == 3037
    assert quote["open_price"] == 3026
    assert quote["volume"] == 884632
    assert quote["position"] == 2020387
    assert quote["bid_price"] == 3036
    assert quote["ask_price"] == 3037
    assert quote["source"] == "vvtr"


def test_is_master_symbol():
    assert is_master_symbol("FG9999")
    assert is_master_symbol("fg9998")
    assert is_master_symbol("ma8888")
    assert not is_master_symbol("rb2610")
    assert not is_master_symbol("rb9999x")
    assert not is_master_symbol("")


def test_v1_message_maps_l1_tick():
    # v1 L1 tick：字段与 snapshot 同构，盘口数组字段名为 quotes，无服务器时间戳
    msg = {
        "s": "RB2610",
        "o": 3026.0,
        "h": 3042.0,
        "l": 3017.0,
        "p": 3037.0,
        "cV": 884632,
        "cA": 26794549120.0,
        "cP": 2020387,
        "tt": 5,
        "lV": 40,
        "lA": 1214730.0,
        "quotes": [{"bP": 3036.0, "bV": 2632, "aP": 3037.0, "aV": 79}],
    }
    quote = vvtr_v1_message_to_quote(msg)
    assert quote is not None
    assert quote["symbol"] == "rb2610"  # v1 回显大写，统一小写
    assert quote["last_price"] == 3037
    assert quote["open_price"] == 3026
    assert quote["volume"] == 884632
    assert quote["position"] == 2020387
    assert quote["turnover"] == 26794549120
    assert quote["bid_price"] == 3036
    assert quote["ask_price"] == 3037
    assert len(quote["bid_levels"]) == 1
    assert quote["source"] == "vvtr"
    # v1 无时间戳：trade_date 由本地时间推算（非空即可）
    assert quote["trade_date"]


def test_v1_message_drops_welcome_and_master():
    assert vvtr_v1_message_to_quote({"type": "welcome", "data": "连接成功"}) is None
    assert vvtr_v1_message_to_quote({"s": "rb9999", "p": 3000}) is None
    assert vvtr_v1_message_to_quote({"s": "rb2610", "p": 0}) is None
