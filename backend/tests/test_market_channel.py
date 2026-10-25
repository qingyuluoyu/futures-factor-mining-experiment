"""行情渠道与 CTP 规范化单测"""

from app.connectors.ctp.normalize import depth_to_quote, instrument_to_symbol
from app.data.quote_schema import filter_valid_quotes, normalize_quote_fields
from app.services.market_channel import (
    DEFAULT_OPENCTP_MD_FRONT_724,
    is_ctp_channel,
    normalize_channel,
    resolve_ctp_login,
    resolve_ctp_md_front,
)


def test_normalize_channel_defaults():
    assert normalize_channel(None) == "sina"
    assert normalize_channel("SIMNOW") == "sina"  # SimNow 已下线：残留值回落 sina
    assert normalize_channel("simnow_724") == "sina"
    assert normalize_channel("openctp_724") == "openctp_724"
    assert normalize_channel("openctp_sim") == "openctp_sim"
    assert normalize_channel("real_ctp") == "real_ctp"
    assert normalize_channel("bad") == "sina"


def test_resolve_ctp_md_front():
    assert is_ctp_channel("openctp_724")
    assert is_ctp_channel("openctp_sim")
    assert is_ctp_channel("real_ctp")
    assert not is_ctp_channel("sina")
    assert not is_ctp_channel("simnow")
    assert resolve_ctp_md_front("openctp_724") == DEFAULT_OPENCTP_MD_FRONT_724
    assert resolve_ctp_md_front("sina") == ""  # 非 CTP 渠道无前置


def test_resolve_ctp_login_openctp(monkeypatch):
    monkeypatch.setenv("OPENCTP_724_INVESTOR", "19659")
    monkeypatch.setenv("OPENCTP_PASSWORD", "123456")
    login = resolve_ctp_login("openctp_724")
    assert login["investor_id"] == "19659"
    assert login["password"] == "123456"
    assert login["broker_id"] == "9999"
    assert "trading.openctp.cn" in login["md_front"]


def test_resolve_ctp_login_real(monkeypatch):
    """真实 CTP 渠道：读 REAL_CTP_* 环境变量"""
    monkeypatch.setenv("REAL_CTP_BROKER_ID", "8010")
    monkeypatch.setenv("REAL_CTP_INVESTOR_ID", "660001")
    monkeypatch.setenv("REAL_CTP_PASSWORD", "secret")
    monkeypatch.setenv("REAL_CTP_MD_FRONT", "tcp://md.example.com:41213")
    monkeypatch.setenv("REAL_CTP_APP_ID", "simnow_client")
    monkeypatch.setenv("REAL_CTP_AUTH_CODE", "0000000000000000")
    login = resolve_ctp_login("real_ctp")
    assert login["channel"] == "real_ctp"
    assert login["broker_id"] == "8010"
    assert login["investor_id"] == "660001"
    assert login["password"] == "secret"
    assert login["md_front"] == "tcp://md.example.com:41213"
    assert login["app_id"] == "simnow_client"
    assert login["auth_code"] == "0000000000000000"
    assert login["user_product_info"] == "qihuo_real"


def test_normalize_quote_fields():
    q = normalize_quote_fields(
        {
            "symbol": "pp2609",
            "last_price": 8500,
            "pre_close": 8400,
            "open_price": 8450,
            "high_price": 8600,
            "low_price": 8300,
            "bid_price": 8499,
            "ask_price": 8501,
            "volume": 100,
            "position": 200,
        }
    )
    assert q is not None
    assert q["symbol"] == "pp2609"
    assert q["change"] == 100.0
    assert q["change_pct"] == round(100 / 8400 * 100, 4)


def test_filter_drops_invalid():
    out = filter_valid_quotes(
        [
            {"symbol": "a", "last_price": 0},
            {"symbol": "b", "last_price": 1.0, "pre_close": 1.0},
        ]
    )
    assert len(out) == 1
    assert out[0]["symbol"] == "b"


def test_instrument_to_symbol():
    assert instrument_to_symbol("PP2609") == "pp2609"


def test_depth_to_quote():
    tick = {
        "InstrumentID": "pp2609",
        "LastPrice": 8500.0,
        "PreSettlementPrice": 8400.0,
        "OpenPrice": 8450.0,
        "HighestPrice": 8600.0,
        "LowestPrice": 8300.0,
        "BidPrice1": 8499.0,
        "AskPrice1": 8501.0,
        "BidVolume1": 10,
        "AskVolume1": 12,
        "BidPrice2": 8498.0,
        "AskPrice2": 8502.0,
        "BidVolume2": 20,
        "AskVolume2": 22,
        "BidPrice3": 8497.0,
        "AskPrice3": 8503.0,
        "BidVolume3": 30,
        "AskVolume3": 32,
        "BidPrice4": 8496.0,
        "AskPrice4": 8504.0,
        "BidVolume4": 40,
        "AskVolume4": 42,
        "BidPrice5": 8495.0,
        "AskPrice5": 8505.0,
        "BidVolume5": 50,
        "AskVolume5": 52,
        "Volume": 12345,
        "OpenInterest": 400000,
        "PreOpenInterest": 390000,
        "Turnover": 524747500.0,
        "SettlementPrice": 8488.0,
        "UpperLimitPrice": 9240.0,
        "LowerLimitPrice": 7560.0,
        "UpdateTime": "14:30:05",
        "UpdateMillisec": 500,
        "ActionDay": "20260724",
    }
    q = depth_to_quote(
        tick,
        {
            "pp2609": {
                "symbol": "pp2609",
                "name": "PP2609",
                "exchange": "大商所",
                "code": "PP",
            }
        },
    )
    assert q is not None
    assert q["last_price"] == 8500.0
    assert q["position"] == 400000
    assert q["tick_time"] == "143005"
    assert q["bid_vol"] == 10
    assert q["pre_open_interest"] == 390000
    assert q["turnover"] == 524747500.0
    assert q["settlement_price"] == 8488.0
    assert q["upper_limit_price"] == 9240.0
    assert q["lower_limit_price"] == 7560.0
    assert q["update_millisec"] == 500
    assert q["bid_levels"] == [
        {"price": 8499.0, "volume": 10},
        {"price": 8498.0, "volume": 20},
        {"price": 8497.0, "volume": 30},
        {"price": 8496.0, "volume": 40},
        {"price": 8495.0, "volume": 50},
    ]
    assert q["ask_levels"] == [
        {"price": 8501.0, "volume": 12},
        {"price": 8502.0, "volume": 22},
        {"price": 8503.0, "volume": 32},
        {"price": 8504.0, "volume": 42},
        {"price": 8505.0, "volume": 52},
    ]


def test_depth_to_quote_drops_ctp_invalid_optional_values():
    q = depth_to_quote(
        {
            "InstrumentID": "pp2609",
            "LastPrice": 8500.0,
            "PreSettlementPrice": 8400.0,
            "SettlementPrice": 1.7976931348623157e308,
            "Turnover": 1.7976931348623157e308,
            "BidPrice1": 1.7976931348623157e308,
            "AskPrice1": 8501.0,
            "AskVolume1": 12,
        },
        {
            "pp2609": {
                "symbol": "pp2609",
                "name": "PP2609",
                "exchange": "大商所",
                "code": "PP",
            }
        },
    )

    assert q is not None
    assert q["settlement_price"] is None
    assert q["turnover"] is None
    assert q["pre_open_interest"] is None
    assert q["bid_levels"] == []
    assert q["ask_levels"] == [{"price": 8501.0, "volume": 12}]


# ---------- sina 源键消费（qihuo-sina-bridge 写源键，本进程只读 Redis） ----------

import json
import time as _time

import pytest

import app.services.market_channel as _mc


@pytest.mark.asyncio
async def test_vvtr_primary_with_sina_source_fallback(monkeypatch):
    """vvtr 推送源供数：主源 vvtr 源键，sina 源键逐字段兜底（无 HTTP 出网）"""

    class _FakeRedis:
        async def get(self, key: str):
            return None

    async def fake_ensure(redis):
        return None

    async def fake_read_channel(redis, mode="live"):
        return "vvtr"

    async def fake_state(redis):
        return {"enabled": True, "quotes_enabled": True,
                "kline_enabled": False, "ws_enabled": True}

    async def fake_collect(redis, source):
        if source == "vvtr":
            return [{
                "symbol": "rb2701", "last_price": 3100.0, "volume": 100,
                "recv_ts": _time.time(), "trade_date": "2026-08-24",
                "source": "vvtr",
            }]
        return [{
            "symbol": "rb2701", "last_price": 3099.0, "pre_close": 3050.0,
            "trade_date": "2026-08-24", "source": "sina",
        }]

    monkeypatch.setattr("app.services.contracts_sync.ensure_active_contracts", fake_ensure)
    monkeypatch.setattr("app.connectors.vvtr.config.read_vvtr_state_from_redis", fake_state)
    monkeypatch.setattr(_mc, "read_channel_from_redis", fake_read_channel)
    monkeypatch.setattr(_mc, "collect_source_quotes_from_redis", fake_collect)
    # 若误走直连新浪立即失败（backend 不再出网）
    import app.data.sina_quote as _sq

    def _must_not_fetch():
        raise AssertionError("backend 不得直连新浪")

    monkeypatch.setattr(_sq, "fetch_sina_quotes", _must_not_fetch)

    quotes = await _mc.fetch_quotes_for_channel(redis_conn=_FakeRedis(), channel="vvtr")
    assert quotes and quotes[0]["symbol"] == "rb2701"
    assert quotes[0]["source"] == "vvtr"  # vvtr 为主源
    # sina 源键补 pre_close（vvtr 帧无昨结字段）
    assert quotes[0].get("pre_close") == 3050.0
    assert quotes[0]["last_price"] == 3100.0


@pytest.mark.asyncio
async def test_no_push_source_sina_source_is_primary(monkeypatch):
    """推送源断流/关闭：sina 源键成为主源（merge 空主源自动以 sina 为主）"""

    class _FakeRedis:
        async def get(self, key: str):
            return None

    async def fake_ensure(redis):
        return None

    async def fake_read_channel(redis, mode="live"):
        return "sina"

    async def fake_collect(redis, source):
        if source == "sina":
            return [{
                "symbol": "rb2701", "last_price": 3090.0, "pre_close": 3050.0,
                "trade_date": "2026-08-24", "source": "sina",
            }]
        return []

    monkeypatch.setattr("app.services.contracts_sync.ensure_active_contracts", fake_ensure)
    monkeypatch.setattr(_mc, "read_channel_from_redis", fake_read_channel)
    monkeypatch.setattr(_mc, "collect_source_quotes_from_redis", fake_collect)

    quotes = await _mc.fetch_quotes_for_channel(redis_conn=_FakeRedis(), channel="sina")
    assert quotes and quotes[0]["source"] == "sina"
    assert quotes[0]["last_price"] == 3090.0


# ---------- ctp_sim（CTP 中转模拟 7×24）----------

def test_normalize_channel_ctp_sim():
    assert normalize_channel("ctp_sim") == "ctp_sim"
    assert is_ctp_channel("ctp_sim")
    # 7×24 模拟渠道不得进入实盘备用候选
    from app.services.market_channel import REALTIME_CHANNELS

    assert "ctp_sim" not in REALTIME_CHANNELS


def test_resolve_ctp_md_front_ctp_sim_default(monkeypatch):
    monkeypatch.delenv("CTP_SIM_MD_FRONT", raising=False)
    from app.services.market_channel import DEFAULT_CTP_SIM_MD_FRONT

    assert resolve_ctp_md_front("ctp_sim") == DEFAULT_CTP_SIM_MD_FRONT
    assert DEFAULT_CTP_SIM_MD_FRONT == ""  # 开源版不内置前置地址


def test_resolve_ctp_login_ctp_sim(monkeypatch):
    """ctp_sim：env 覆盖默认前置/账号（管理端 Redis 镜像由 bridge 侧覆盖）"""
    monkeypatch.setenv("CTP_SIM_MD_FRONT", "tcp://1.2.3.4:17802")
    monkeypatch.setenv("CTP_SIM_BROKER_ID", "2001")
    monkeypatch.setenv("CTP_SIM_INVESTOR_ID", "tester")
    monkeypatch.setenv("CTP_SIM_PASSWORD", "anypass")
    login = resolve_ctp_login("ctp_sim")
    assert login["channel"] == "ctp_sim"
    assert login["broker_id"] == "2001"
    assert login["investor_id"] == "tester"
    assert login["password"] == "anypass"
    assert login["md_front"] == "tcp://1.2.3.4:17802"
    assert login["app_id"] == ""  # 任意账号可登录，无需认证


def test_resolve_ctp_login_ctp_sim_defaults(monkeypatch):
    for k in (
        "CTP_SIM_MD_FRONT",
        "CTP_SIM_BROKER_ID",
        "CTP_SIM_INVESTOR_ID",
        "CTP_SIM_PASSWORD",
    ):
        monkeypatch.delenv(k, raising=False)
    login = resolve_ctp_login("ctp_sim")
    assert login["broker_id"] == "9999"
    assert login["investor_id"] == "qihuo_sim"
    assert login["password"] == "qihuo_sim"
    assert login["md_front"] == ""  # 开源版不内置前置地址


@pytest.mark.asyncio
async def test_ctp_sim_primary_with_sina_source_fallback(monkeypatch):
    """ctp_sim 渠道：桥源键作主源，sina 逐字段兜底；sim 空则回落 sina"""

    class _FakeRedis:
        async def get(self, key: str):
            return None

    async def fake_ensure(redis):
        return None

    async def fake_read_channel(redis, mode="live"):
        return "ctp_sim"

    async def fake_collect(redis, source):
        if source == "ctp_sim":
            return [{
                "symbol": "m2701", "last_price": 2998.0, "pre_close": 0.0,
                "trade_date": "2026-10-11", "source": "ctp_sim",
                "recv_ts": __import__("time").time(),
            }]
        if source == "sina":
            return [{
                "symbol": "rb2701", "last_price": 3090.0, "pre_close": 3050.0,
                "trade_date": "2026-10-09", "source": "sina",
            }]
        return []

    monkeypatch.setattr("app.services.contracts_sync.ensure_active_contracts", fake_ensure)
    monkeypatch.setattr(_mc, "read_channel_from_redis", fake_read_channel)
    monkeypatch.setattr(_mc, "collect_source_quotes_from_redis", fake_collect)

    quotes = await _mc.fetch_quotes_for_channel(redis_conn=_FakeRedis(), channel="ctp_sim")
    by_symbol = {q["symbol"]: q for q in quotes}
    assert by_symbol["m2701"]["source"] == "ctp_sim"
    assert by_symbol["m2701"]["last_price"] == 2998.0
    # 仅 sina 存在的合约保留 sina 价
    assert by_symbol["rb2701"]["last_price"] == 3090.0


@pytest.mark.asyncio
async def test_ctp_sim_empty_falls_back_sina(monkeypatch):
    """ctp_sim 渠道源键为空（桥未启/断流）：整表回落 sina 主源"""

    class _FakeRedis:
        async def get(self, key: str):
            return None

    async def fake_ensure(redis):
        return None

    async def fake_read_channel(redis, mode="live"):
        return "ctp_sim"

    async def fake_collect(redis, source):
        if source == "sina":
            return [{
                "symbol": "rb2701", "last_price": 3090.0, "pre_close": 3050.0,
                "trade_date": "2026-10-09", "source": "sina",
            }]
        return []

    monkeypatch.setattr("app.services.contracts_sync.ensure_active_contracts", fake_ensure)
    monkeypatch.setattr(_mc, "read_channel_from_redis", fake_read_channel)
    monkeypatch.setattr(_mc, "collect_source_quotes_from_redis", fake_collect)

    quotes = await _mc.fetch_quotes_for_channel(redis_conn=_FakeRedis(), channel="ctp_sim")
    assert quotes and quotes[0]["source"] == "sina"


@pytest.mark.asyncio
async def test_write_ctp_sim_config_to_redis():
    from app.services.market_channel import CTP_SIM_CONFIG_KEY, write_ctp_sim_config_to_redis

    class _FakeRedis:
        def __init__(self):
            self.data: dict[str, str] = {}

        async def set(self, key, value, ex=None):
            self.data[key] = value

        async def get(self, key):
            return self.data.get(key)

    r = _FakeRedis()
    await write_ctp_sim_config_to_redis(
        r,
        md_front="tcp://my-front:17802",
        broker_id="9999",
        investor_id="qihuo_sim",
        password="anypass",
    )
    import json as _json

    cfg = _json.loads(r.data[CTP_SIM_CONFIG_KEY])
    assert cfg == {
        "md_front": "tcp://my-front:17802",
        "broker_id": "9999",
        "investor_id": "qihuo_sim",
        "password": "anypass",
    }
