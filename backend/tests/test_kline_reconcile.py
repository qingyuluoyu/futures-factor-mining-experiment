"""盘中对账 reconcile_forming_with_vvtr：权威替换 + 版本递增 + 修正标记"""

from __future__ import annotations

import json

import pytest

from app.services import kline as kline_svc
from app.services.kline import reconcile_forming_with_vvtr


class _FakeRedis:
    def __init__(self, quotes: dict[str, str] | None = None) -> None:
        self.store: dict[str, str] = dict(quotes or {})

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value


def _state(bar_time: str, version: int = 3) -> dict:
    return {
        "bar": {
            "time": bar_time,
            "open": 4000.0,
            "high": 4010.0,
            "low": 3990.0,
            "close": 4005.0,
            "volume": 100,
            "settle": None,
            "open_interest": None,
            "version": version,
        },
        "bar_time": bar_time,
        "period_start_volume": 1000,
        "open_locked": True,
        "version": version,
    }


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(kline_svc, "_realtime_state", {})
    yield


def _patch_fetch(monkeypatch, result: dict):
    import app.connectors.vvtr.http_client as http_client

    monkeypatch.setattr(
        http_client, "fetch_vvtr_current_bars", lambda *a, **k: result
    )
    # 跳过配置读取（直接返回启用态）
    import app.connectors.vvtr.config as vvtr_config

    class _Cfg:
        api_key = "k"
        kline_enabled = True
        base_url = "http://relay"

    monkeypatch.setattr(
        vvtr_config, "get_vvtr_config_sync", lambda: _Cfg()
    )


@pytest.mark.asyncio
async def test_reconcile_replaces_on_material_diff(monkeypatch):
    kline_svc._realtime_state["rb2610"] = {"1m": _state("2026-08-22 09:01:00")}
    auth = {
        "time": "2026-08-22 09:01:00",
        "open": 4000.0,
        "high": 4020.0,  # 权威 high 更高（丢过帧）
        "low": 3990.0,
        "close": 4006.0,
        "volume": 120,
    }
    _patch_fetch(monkeypatch, {"rb2610": auth})

    redis = _FakeRedis(
        {"market:live:quote:rb2610": json.dumps({"volume": 1120})}
    )
    n = await reconcile_forming_with_vvtr(redis, ["rb2610"], periods=("1m",))

    assert n == 1
    state = kline_svc._realtime_state["rb2610"]["1m"]
    bar = state["bar"]
    assert bar["high"] == 4020.0
    assert bar["close"] == 4006.0
    assert bar["volume"] == 120
    assert bar["version"] == 4  # 3 → 4 递增
    assert bar["kind"] == "correction"
    # 增量基线按 当日累计量 - 当根权威量 重建
    assert state["period_start_volume"] == 1120 - 120
    assert not state.get("recovered")
    # 修正后写回 Redis
    payload = json.loads(redis.store["kline:realtime:rb2610:1m"])
    assert payload["bar"]["kind"] == "correction"


@pytest.mark.asyncio
async def test_reconcile_skips_when_identical(monkeypatch):
    """权威值与本地一致 → 不动（不产生无谓修正帧）"""
    kline_svc._realtime_state["rb2610"] = {"1m": _state("2026-08-22 09:01:00")}
    _patch_fetch(
        monkeypatch,
        {
            "rb2610": {
                "time": "2026-08-22 09:01:00",
                "open": 4000.0,
                "high": 4010.0,
                "low": 3990.0,
                "close": 4005.0,
                "volume": 100,
            }
        },
    )
    redis = _FakeRedis({"market:live:quote:rb2610": json.dumps({"volume": 1100})})
    n = await reconcile_forming_with_vvtr(redis, ["rb2610"], periods=("1m",))
    assert n == 0
    assert kline_svc._realtime_state["rb2610"]["1m"]["version"] == 3


@pytest.mark.asyncio
async def test_reconcile_skips_when_time_key_mismatch(monkeypatch):
    """上游滞后（权威 bar 时间键 ≠ 当前桶）→ 保守跳过"""
    kline_svc._realtime_state["rb2610"] = {"1m": _state("2026-08-22 09:05:00")}
    _patch_fetch(
        monkeypatch,
        {
            "rb2610": {
                "time": "2026-08-22 09:04:00",  # 上一根
                "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
                "volume": 1,
            }
        },
    )
    redis = _FakeRedis({"market:live:quote:rb2610": json.dumps({"volume": 1100})})
    n = await reconcile_forming_with_vvtr(redis, ["rb2610"], periods=("1m",))
    assert n == 0
    assert kline_svc._realtime_state["rb2610"]["1m"]["bar"]["close"] == 4005.0


@pytest.mark.asyncio
async def test_reconcile_skips_without_volume_baseline(monkeypatch):
    """quote 基线不可得（无累计量）→ 跳过，保留原值"""
    kline_svc._realtime_state["rb2610"] = {"1m": _state("2026-08-22 09:01:00")}
    _patch_fetch(
        monkeypatch,
        {
            "rb2610": {
                "time": "2026-08-22 09:01:00",
                "open": 4000.0, "high": 4020.0, "low": 3990.0,
                "close": 4006.0, "volume": 120,
            }
        },
    )
    redis = _FakeRedis()  # 无 market:live:quote:*
    n = await reconcile_forming_with_vvtr(redis, ["rb2610"], periods=("1m",))
    assert n == 0
