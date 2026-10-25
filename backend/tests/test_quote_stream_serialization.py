"""行情流序列号与时间戳补齐测试 —— openspec 2026-08-23-quote-stream-serialization"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.api import ws as ws_api
from app.data.quote_schema import normalize_quote_fields, normalize_tick_time_iso
from app.services.kline import _exchange_clock
from app.services.market import generate_tick_trade

_BJ_TZ = timezone(timedelta(hours=8))


# ============ A. tick_time 归一 ============


def test_normalize_tick_time_iso_variants():
    # VVTR v2 17 位毫秒数字
    assert normalize_tick_time_iso("20260821143205123") == "2026-08-21 14:32:05"
    # iso 带毫秒 / T 分隔 / 纯 iso
    assert normalize_tick_time_iso("2026-08-21 14:32:05.123") == "2026-08-21 14:32:05"
    assert normalize_tick_time_iso("2026-08-21T14:32:05") == "2026-08-21 14:32:05"
    assert normalize_tick_time_iso("2026-08-21 14:32:05") == "2026-08-21 14:32:05"
    # 纯时刻(新浪/CTP 展示形态,无日期)→ None(跨夜不可靠)
    assert normalize_tick_time_iso("14:32:05") is None
    assert normalize_tick_time_iso("143205") is None
    # 垃圾/空
    assert normalize_tick_time_iso("") is None
    assert normalize_tick_time_iso(None) is None
    assert normalize_tick_time_iso("abc") is None


def test_normalize_quote_fields_outputs_iso():
    q = normalize_quote_fields(
        {
            "symbol": "rb2610",
            "last_price": 3100.0,
            "tick_time": "20260821093100123",
        }
    )
    assert q is not None
    assert q["tick_time_iso"] == "2026-08-21 09:31:00"
    assert q["tick_time"] == "20260821093100123"  # 原样保留(UI 兼容)

    q2 = normalize_quote_fields(
        {"symbol": "rb2610", "last_price": 3100.0, "tick_time": "09:31:00"}
    )
    assert q2 is not None
    assert q2["tick_time_iso"] is None


# ============ B. forming 时间基准优先 iso ============


def test_exchange_clock_prefers_iso():
    now = datetime(2026, 8, 21, 9, 31, 20, tzinfo=_BJ_TZ)
    # iso 与本地钟偏差 <5 分钟 → 采用交易所时间(整分对齐)
    q = {"tick_time_iso": "2026-08-21 09:31:00", "tick_time": "20260821093100123"}
    assert _exchange_clock(q, now) == datetime(2026, 8, 21, 9, 31, 0, tzinfo=_BJ_TZ)
    # iso 超过漂移防护(>5 分钟)→ 回退本地钟
    q_stale = {"tick_time_iso": "2026-08-21 09:00:00"}
    assert _exchange_clock(q_stale, now) == now
    # 无 iso → 回退旧分支(17 位数字仍可用)
    q_legacy = {"tick_time": "20260821093100123"}
    assert _exchange_clock(q_legacy, now) == datetime(
        2026, 8, 21, 9, 31, 0, tzinfo=_BJ_TZ
    )
    # 新浪纯时刻 → 本地钟(语义不变)
    q_pure = {"tick_time": "09:31:00"}
    assert _exchange_clock(q_pure, now) == now


# ============ C. trades 完整时间 ============


def _panel_with_delta(vol: int) -> dict[str, Any]:
    return {"stats": {"current_volume": vol}, "last_direction": "buy"}


def test_generate_tick_trade_full_ts():
    quote = {
        "symbol": "rb2610",
        "last_price": 3100.0,
        "tick_time": "2026-08-20 22:30:05",  # 夜盘(归属 08-21 交易日)
        "trade_date": "2026-08-21",
    }
    trades = generate_tick_trade(_panel_with_delta(50), quote)
    assert len(trades) == 1
    t = trades[0]
    assert t["time"] == "22:30:05"  # 展示兼容
    assert t["ts"] == "2026-08-21 22:30:05"  # 交易日 + 时刻


def test_generate_tick_trade_ts_fallback_without_trade_date():
    quote = {"symbol": "rb2610", "last_price": 3100.0, "tick_time": "143205"}
    trades = generate_tick_trade(_panel_with_delta(7), quote)
    assert len(trades) == 1
    t = trades[0]
    assert t["time"] == "14:32:05"
    # trade_date 缺失 → 服务器交易日归属(格式 YYYY-MM-DD,可解析)
    date_part = t["ts"][:10]
    datetime.strptime(date_part, "%Y-%m-%d")
    assert t["ts"].endswith("14:32:05")


# ============ D. 出网序列号与回退帧过滤 ============


def test_stamp_and_filter_quotes():
    ws_api._LAST_QUOTE_RECV.clear()
    base = ["rb2610", "j2701"]
    quotes = [
        {"symbol": "rb2610", "last_price": 1.0, "recv_ts": 100.0},
        {"symbol": "j2701", "last_price": 2.0, "recv_ts": 100.0},
        {"symbol": "rb2610", "last_price": 1.5, "recv_ts": 99.0},  # 回退帧
        {"symbol": "rb2610", "last_price": 2.0, "recv_ts": 101.0},
        {"symbol": "weird"},  # 无 symbol 丢弃
        {"symbol": "v2701", "last_price": 3.0},  # 无 recv_ts:不丢只盖 seq
    ]
    out = ws_api._stamp_and_filter_quotes("live", quotes)
    syms = [q["symbol"] for q in out]
    assert syms == ["rb2610", "j2701", "rb2610", "v2701"]  # 回退帧/无symbol被拦
    seqs = [q["seq"] for q in out]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)  # 严格递增
    assert all(s > 1e12 for s in seqs)  # epoch 毫秒基数(重启安全)
    # 状态已记录:再来的更旧帧全被拦
    out2 = ws_api._stamp_and_filter_quotes(
        "live", [{"symbol": "rb2610", "last_price": 9.0, "recv_ts": 50.0}]
    )
    assert out2 == []
    # 不同 mode 互不影响
    out3 = ws_api._stamp_and_filter_quotes(
        "virtual", [{"symbol": "rb2610", "last_price": 9.0, "recv_ts": 50.0}]
    )
    assert len(out3) == 1


def test_stamp_and_filter_restart_safe_base():
    """seq 基数 = epoch 毫秒:重启后新计数不会小于重启前"""
    assert ws_api._SEQ_BASE >= 1_700_000_000_000  # 2023 之后
    ws_api._LAST_QUOTE_RECV.clear()
    out = ws_api._stamp_and_filter_quotes(
        "live", [{"symbol": "rb2610", "recv_ts": 1.0, "last_price": 1.0}]
    )
    assert out[0]["seq"] >= ws_api._SEQ_BASE


# ============ 涨跌幅基准(2026-08-24 0.00% 事故修复) ============


def test_normalize_settlement_as_pre_fallback():
    """昨结算兜底基准(VVTR v2 帧带 pre_settle):涨跌幅按昨结算算"""
    q = normalize_quote_fields(
        {
            "symbol": "rb2610",
            "last_price": 3100.0,
            "settlement_price": 3050.0,  # pre_settle
        }
    )
    assert q["pre_close"] == 3050.0
    assert q["change"] == 50.0
    assert q["change_pct"] == round(50.0 / 3050.0 * 100, 4)


def test_normalize_no_base_outputs_none_not_zero():
    """昨收/昨结算均缺(VVTR v1 深度帧):change/pct/pre_close 为 None,
    绝不输出误导性 0.00%"""
    q = normalize_quote_fields(
        {"symbol": "sp2701", "last_price": 5034.0}
    )
    assert q["pre_close"] is None
    assert q["change"] is None
    assert q["change_pct"] is None
    assert q["last_price"] == 5034.0


def test_normalize_sina_style_unchanged():
    """自带基准与 change 的源(新浪/CTP)行为不变"""
    q = normalize_quote_fields(
        {
            "symbol": "rb2610",
            "last_price": 3100.0,
            "pre_close": 3050.0,
            "change": 50.0,
            "change_pct": 1.64,
        }
    )
    assert q["pre_close"] == 3050.0
    assert q["change"] == 50.0
    assert q["change_pct"] == 1.64


async def test_ws_bridge_flush_backfills_prev_close(monkeypatch):
    """ws_bridge flush:无基准 quote 优先同合约 sina 昨结,缺失用品种昨收补"""
    from unittest.mock import AsyncMock

    import app.connectors.vvtr.ws_bridge as wb

    class _Pipe:
        def __init__(self):
            self.items: list[tuple[str, str]] = []

        def set(self, key, value, ex=None):
            self.items.append((key, value))
            return self

        async def execute(self):
            return None

    class _FakeRedis:
        def __init__(self, sina_quotes: dict[str, str] | None = None):
            self._pipe = _Pipe()
            self._sina = sina_quotes or {}

        def pipeline(self, transaction=False):
            return self._pipe  # 单例:flush 后测试可读取写入项

        async def mget(self, keys):
            # 同合约 sina 源键查询（sina 真昨结兜底优先级）
            return [self._sina.get(k) for k in keys]

    # sp2701 无 sina 源数据 → 品种昨收兜底;rb2610 已有基准不动
    bridge = wb.VvtrWsBridge(_FakeRedis(), None)
    bridge._prev_close = {"sp": 5000.0}
    bridge._prev_close_at = wb._now()  # 新鲜,不触发刷新
    bridge._latest = {
        "sp2701": {"symbol": "sp2701", "last_price": 5034.0, "pre_close": None},
        "rb2610": {"symbol": "rb2610", "last_price": 3100.0, "pre_close": 3050.0},
    }
    n = await bridge._flush_latest()
    assert n == 2
    sp = json.loads(bridge._prev_close and bridge._redis.pipeline().items[0][1])
    assert sp["pre_close"] == 5000.0
    assert sp["change"] == 34.0
    assert sp["change_pct"] == round(34.0 / 5000.0 * 100, 4)
    rb = json.loads(bridge._redis.pipeline().items[1][1])
    assert rb["pre_close"] == 3050.0  # 已有基准不动


async def test_ws_bridge_flush_prefers_sina_prev_settle(monkeypatch):
    """ws_bridge flush:无基准且同合约 sina 源有昨结时优先用真昨结"""
    import app.connectors.vvtr.ws_bridge as wb
    from app.services.market_keys import source_quote_key

    class _Pipe:
        def __init__(self):
            self.items: list[tuple[str, str]] = []

        def set(self, key, value, ex=None):
            self.items.append((key, value))
            return self

        async def execute(self):
            return None

    sina_key = source_quote_key("sina", "ni2610")
    # sina 昨结 128200(真值) vs 品种昨收兜底 128680(近似,应被压制)
    fake = type("FR", (), {})()
    fake._pipe = _Pipe()
    fake._sina = {sina_key: json.dumps({"symbol": "ni2610", "pre_close": 128200.0})}
    fake.pipeline = lambda transaction=False: fake._pipe
    fake.mget = _make_async_mget(fake._sina)

    bridge = wb.VvtrWsBridge(fake, None)
    bridge._prev_close = {"ni": 128680.0}
    bridge._prev_close_at = wb._now()
    bridge._latest = {
        "ni2610": {"symbol": "ni2610", "last_price": 126820.0, "pre_close": None},
    }
    n = await bridge._flush_latest()
    assert n == 1
    ni = json.loads(fake._pipe.items[0][1])
    assert ni["pre_close"] == 128200.0  # sina 真昨结优先
    assert ni["change_pct"] == round((126820.0 - 128200.0) / 128200.0 * 100, 4)


def _make_async_mget(store: dict[str, str]):
    async def mget(keys):
        return [store.get(k) for k in keys]

    return mget


# ============ C2. trades 帧四路开平（成交量分布秒级增量） ============


def _panel_with_delta_and_oi(vol: int, oi_delta: Any, direction: str = "buy") -> dict[str, Any]:
    return {
        "stats": {
            "current_volume": vol,
            "oi_delta": (
                {"value": oi_delta, "source": "derived", "quality": "derived"}
                if oi_delta is not None
                else None
            ),
        },
        "last_direction": direction,
    }


def test_generate_tick_trade_carries_four_way_split():
    """双开秒（主动买 + ΔOI=量）→ 帧带 open_long=量，其余 0"""
    quote = {"symbol": "rb2701", "last_price": 3250.0, "tick_time": "093105", "trade_date": "2026-09-17"}
    trades = generate_tick_trade(_panel_with_delta_and_oi(120, 120), quote)
    assert len(trades) == 1
    t = trades[0]
    assert t["open_long"] == 120
    assert t["close_long"] == 0
    assert t["open_short"] == 0
    assert t["close_short"] == 0


def test_generate_tick_trade_four_way_sell_turnover():
    """主动卖 + 持仓不变（换手）→ 卖侧对半拆开空/平多"""
    quote = {"symbol": "rb2701", "last_price": 3250.0, "tick_time": "093106", "trade_date": "2026-09-17"}
    trades = generate_tick_trade(_panel_with_delta_and_oi(80, 0, direction="sell"), quote)
    t = trades[0]
    assert t["open_short"] == 40
    assert t["close_long"] == 40
    assert t["open_long"] == 0
    assert t["close_short"] == 0


def test_generate_tick_trade_four_way_none_without_oi():
    """oi_delta 缺失 → 四路 None（前端只累积总手）"""
    quote = {"symbol": "rb2701", "last_price": 3250.0, "tick_time": "093107", "trade_date": "2026-09-17"}
    trades = generate_tick_trade(_panel_with_delta_and_oi(50, None), quote)
    t = trades[0]
    assert t["open_long"] is None
    assert t["close_long"] is None
    assert t["open_short"] is None
    assert t["close_short"] is None
