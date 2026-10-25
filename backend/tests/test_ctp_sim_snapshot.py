"""ctp_sim 快照源桥行为单测 —— 保温重写 / 自愈旁路 / 渠道构建"""

from __future__ import annotations

import json

import pytest

from app.connectors.ctp.md_bridge import CtpMdBridge, build_bridge_from_env


def _bridge(channel: str = "ctp_sim", mode: str = "live") -> CtpMdBridge:
    return CtpMdBridge(
        broker_id="9999",
        investor_id="u",
        password="p",
        md_front="tcp://1.2.3.4:17802",
        redis_url="redis://x",
        app_id="",
        auth_code="",
        user_product_info="q",
        channel_label=channel,
        trading_mode=mode,
        allow_off_hours=True,
    )


class _FakeRedis:
    def __init__(self):
        self.data: dict[str, str] = {}

    def ping(self):
        return True

    def set(self, key, value, ex=None):
        self.data[key] = value


def test_snapshot_source_flags():
    b = _bridge("ctp_sim")
    assert b._snapshot_source is True
    # 快照源不做停滞自愈（_check_tick_health 直接返回，不退出进程）
    b._check_tick_health()
    b2 = _bridge("real_ctp")
    assert b2._snapshot_source is False


def test_snapshot_keep_warm_refreshes_recv_ts(monkeypatch):
    """缓存 quote 周期重写：recv_ts 刷新、价格不变、source 标注渠道"""
    b = _bridge("ctp_sim")
    r = _FakeRedis()
    b._redis = r
    # 模拟首订快照落缓存（symbol 与 ALL_CONTRACTS 对齐）
    b._snapshot_cache = {
        "m2701": {"symbol": "m2701", "last_price": 2998.0},
    }
    b._snapshot_keep_warm()
    payload = json.loads(r.data["market:live:source:ctp_sim:quote:m2701"])
    assert payload["source"] == "ctp_sim"
    assert payload["last_price"] == 2998.0
    assert "recv_ts" in payload
    # 二次保温：recv_ts 单调不减
    first_ts = payload["recv_ts"]
    b._snapshot_keep_warm()
    payload2 = json.loads(r.data["market:live:source:ctp_sim:quote:m2701"])
    assert payload2["recv_ts"] >= first_ts


def test_build_bridge_from_env_channel_and_off_hours(monkeypatch):
    """强制渠道 ctp_sim + off_hours；virtual 默认回落 openctp_724"""
    monkeypatch.setenv("CTP_BRIDGE_MODE", "live")
    monkeypatch.setenv("CTP_BRIDGE_CHANNEL", "ctp_sim")
    monkeypatch.setenv("REDIS_URL", "redis://fake")
    monkeypatch.delenv("CTP_BRIDGE_VIRTUAL_CHANNEL", raising=False)
    b = build_bridge_from_env()
    assert b.channel_label == "ctp_sim"
    assert b.md_front == ""  # 开源版不内置前置地址
    assert b.allow_off_hours is True

    monkeypatch.setenv("CTP_BRIDGE_MODE", "virtual")
    monkeypatch.delenv("CTP_BRIDGE_CHANNEL", raising=False)
    b2 = build_bridge_from_env()
    assert b2.channel_label == "openctp_724"
    assert b2.trading_mode == "virtual"


def test_build_bridge_virtual_channel_override(monkeypatch):
    """CTP_BRIDGE_VIRTUAL_CHANNEL=ctp_sim：虚拟盘 7×24 换中转模拟源"""
    monkeypatch.setenv("CTP_BRIDGE_MODE", "virtual")
    monkeypatch.setenv("CTP_BRIDGE_VIRTUAL_CHANNEL", "ctp_sim")
    monkeypatch.setenv("REDIS_URL", "redis://fake")
    b = build_bridge_from_env()
    assert b.channel_label == "ctp_sim"
    assert b.allow_off_hours is True  # virtual 本就允许 + ctp_sim 双保险


def test_build_bridge_virtual_channel_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("CTP_BRIDGE_MODE", "virtual")
    monkeypatch.setenv("CTP_BRIDGE_VIRTUAL_CHANNEL", "not_a_channel")
    monkeypatch.setenv("REDIS_URL", "redis://fake")
    b = build_bridge_from_env()
    assert b.channel_label == "openctp_724"
