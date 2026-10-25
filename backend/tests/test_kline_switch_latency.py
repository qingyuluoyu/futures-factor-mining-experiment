"""分时 K 线切换慢修复的回归测试

根因（2026-08-20 实测，冷切换 1m 需 3.7-5s）：
1. 双重节流——backend 与 relay 抢同一把 Redis 节流锁，每个上游请求
   排两次队（≥2.2s）；客户端节流改为仅直连官方时生效；
2. 当日段 to=now 秒级变化打散 relay 缓存键——同分钟内切换周期也
   全额重拉；缓存键对秒级时间参数归整到分钟。
"""

from fastapi.testclient import TestClient

import app.relay.main as relay
from app.connectors.vvtr.http_client import throttle_applies
from app.relay.main import RelaySettings, create_app


# ---------- 客户端节流范围 ----------


def test_throttle_applies_only_direct_to_official():
    assert throttle_applies("https://rest.vvtr.com/v1") is True
    assert throttle_applies("https://rest.vvtr.com") is True
    assert throttle_applies("") is True  # 空 → 默认官方
    # relay（任何非官方主机，含 Phase 2 的 58）由 relay 统一预算
    assert throttle_applies("http://qihuo-vvtr-relay:8100/v1") is False
    assert throttle_applies("http://192.0.2.58:8100/v1") is False


# ---------- relay 缓存键秒级归整 ----------


def test_kline_cache_key_floors_seconds_within_minute():
    base = {
        "symbols": "rb2701",
        "interval": "1m",
        "from": "2026-08-20 00:00:00",
        "to": "2026-08-20 22:30:45",
    }
    same_minute = dict(base, to="2026-08-20 22:30:07")
    next_minute = dict(base, to="2026-08-20 22:31:00")
    key = lambda p: relay.cache_key(
        "kline/history", relay._cache_key_params("kline/history", p)
    )
    assert key(base) == key(same_minute)  # 同分钟 → 共享缓存
    assert key(base) != key(next_minute)
    # 非时间参数与日期参数不归整
    snap = {"symbols": "rb2701", "from": "2026-08-19", "to": "2026-08-20"}
    assert relay._cache_key_params("snapshot", snap) == snap
    assert relay._cache_key_params("kline/history", snap) == snap


def test_kline_proxy_same_minute_shares_cache(monkeypatch):
    """同分钟内两次切换（to 秒位不同）只打一次上游"""
    seen: list[str] = []

    def fake_forward(settings_, sub, params, rate_):
        seen.append(str(params.get("to")))
        return relay.CachedResponse(200, "application/json", b'{"code":200,"data":{}}')

    monkeypatch.setattr(relay, "_forward_sync", fake_forward)
    app = create_app(RelaySettings(api_key="k"), start_upstream=False)
    with TestClient(app) as client:
        r1 = client.get(
            "/v1/kline/history?symbols=rb2701&interval=1m"
            "&from=2026-08-20%2000:00:00&to=2026-08-20%2022:30:45&apiKey=a"
        )
        r2 = client.get(
            "/v1/kline/history?symbols=rb2701&interval=1m"
            "&from=2026-08-20%2000:00:00&to=2026-08-20%2022:30:07&apiKey=b"
        )
        stats = client.get("/stats").json()
    assert r1.status_code == 200 and r2.status_code == 200
    assert len(seen) == 1  # 第二次命中缓存，不打上游
    assert seen[0] == "2026-08-20 22:30:45"  # 转发参数保持原样（不归整）
    assert stats["cache_hits"] == 1
