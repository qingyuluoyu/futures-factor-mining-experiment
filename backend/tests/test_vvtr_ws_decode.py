"""VVTR WSS v1 解码回归测试 —— 双重 JSON 编码（2026-08-20 实测抓包）

v1 推送外层数组元素是再包一层 JSON 的字符串；只解外层会把字符串
直接丢给映射器 → isinstance(msg, dict) 不成立 → 全部丢弃 →
HTTP 兜底持续供数（33 上 symbols=0 的根因）。
"""

import asyncio

from app.connectors.vvtr.quote_map import (
    parse_vvtr_frames,
    vvtr_v1_message_to_quote,
)

# 实抓 v1 帧（wss://rest.vvtr.com/v1/connect，2026-08-20 14:38 盘中）
WELCOME_FRAME = (
    '{"type":"welcome","data":"连接成功 (V1)，已订阅: type=14: '
    'P2701,AU2610,M2701,RB2610,TA2701,I2701,HC2610,CU2609,AL2609,ZN2609",'
    '"time":1787207888}'
)

DATA_FRAME = (
    '["{\\"t\\":\\"2026-08-20 14:38:08\\",\\"s\\":\\"HC2610\\",\\"o\\":3287,'
    '\\"h\\":3300,\\"l\\":3281,\\"p\\":3290,\\"cV\\":285351,'
    '\\"cA\\":9.38714978e+09,\\"lV\\":1,\\"lA\\":32900,\\"cP\\":1.151194e+06,'
    '\\"tt\\":2,\\"iopv\\":0,\\"quotes\\":[{\\"aP\\":3290,\\"aV\\":289,'
    '\\"bP\\":3289,\\"bV\\":877}]}",'
    '"{\\"t\\":\\"2026-08-20 14:38:08\\",\\"s\\":\\"AL2609\\",\\"o\\":23650,'
    '\\"h\\":23705,\\"l\\":23525,\\"p\\":23595,\\"cV\\":69376,'
    '\\"cA\\":8.193516e+09,\\"lV\\":0,\\"lA\\":0,\\"cP\\":129593,'
    '\\"tt\\":7,\\"iopv\\":0,\\"quotes\\":[{\\"aP\\":23595,\\"aV\\":70,'
    '\\"bP\\":23590,\\"bV\\":44}]}"]'
)


def test_parse_double_encoded_data_frame():
    """v1 数据帧：外层字符串元素必须二次解出 dict"""
    frames = parse_vvtr_frames(DATA_FRAME)
    assert len(frames) == 2
    assert all(isinstance(f, dict) for f in frames)
    assert frames[0]["s"] == "HC2610"
    assert frames[0]["p"] == 3290
    assert frames[1]["s"] == "AL2609"


def test_parse_welcome_and_garbage():
    assert parse_vvtr_frames(WELCOME_FRAME) == [
        {
            "type": "welcome",
            "data": (
                "连接成功 (V1)，已订阅: type=14: "
                "P2701,AU2610,M2701,RB2610,TA2701,I2701,HC2610,CU2609,"
                "AL2609,ZN2609"
            ),
            "time": 1787207888,
        }
    ]
    assert parse_vvtr_frames("ping") == []
    assert parse_vvtr_frames("") == []
    assert parse_vvtr_frames('["坏元素",123]') == []


def test_v1_mapper_real_message():
    quote = vvtr_v1_message_to_quote(parse_vvtr_frames(DATA_FRAME)[0])
    assert quote is not None
    assert quote["symbol"] == "hc2610"
    assert quote["last_price"] == 3290.0
    assert quote["tick_time"] == "2026-08-20 14:38:08"
    assert quote["trade_date"] == "2026-08-20"  # 日盘归当日
    assert quote["bid_price"] == 3289.0 and quote["bid_vol"] == 877
    assert quote["ask_price"] == 3290.0 and quote["ask_vol"] == 289
    assert quote["position"] == 1151194
    assert quote["volume"] == 285351
    assert quote["turnover"] == 9387149780.0


def test_v1_mapper_night_session_rolls_trade_date():
    """夜盘 20:00 后归属下一工作日（周四夜 → 周五）"""
    msg = {
        "t": "2026-08-20 21:30:00",  # 周四夜盘
        "s": "RB2610",
        "o": 3017, "h": 3037, "l": 3013, "p": 3021,
        "cV": 100, "cA": 1.0, "cP": 1000,
        "quotes": [{"aP": 3022, "aV": 1, "bP": 3021, "bV": 2}],
    }
    quote = vvtr_v1_message_to_quote(msg)
    assert quote is not None
    assert quote["trade_date"] == "2026-08-21"


def test_v1_mapper_drops_synth_master():
    assert vvtr_v1_message_to_quote({"s": "CU9999", "p": 100.0}) is None


def test_bridge_on_message_feeds_latest():
    """端到端回归：桥收 v1 双重编码帧后必须进入 _latest（原 bug 全丢弃）"""
    from app.connectors.vvtr.ws_bridge import VvtrWsBridge

    bridge = VvtrWsBridge(redis_conn=None, session_factory=None)
    bridge._ws_version = 1

    asyncio.run(bridge._on_message(DATA_FRAME))

    assert bridge._subscribed_symbols > 0
    assert set(bridge._latest) == {"hc2610", "al2609"}
    assert bridge._latest["hc2610"]["last_price"] == 3290.0
