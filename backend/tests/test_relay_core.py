"""relay 核心逻辑单测 —— 预检门控 / apiKey 替换与脱敏 / 缓存 TTL /
帧透传广播 / 订阅清单 cap / 主力只进不退（计划 4.5）

不触网：上游会话、节流器均不启动；HTTP 面用假 _forward_sync。
"""

import asyncio
import json
import time

from fastapi.testclient import TestClient

import app.relay.main as relay
from app.connectors.vvtr.http_client import _mask
from app.relay.main import (
    DownstreamHub,
    PacingWindow,
    PeerGate,
    RelaySettings,
    RelayStats,
    RateTracker,
    TTLCache,
    UpstreamManager,
    apply_master_refresh,
    build_subscription,
    build_welcome_frame,
    cache_key,
    create_app,
    extra_watch_symbols,
    initial_masters,
    strip_api_key,
    translate_v1_frame,
)


# ---------- v1→v2 帧形翻译（套餐暂仅 v1，下游桥按 v2 会话解析） ----------


def test_translate_v1_frame_field_mapping():
    """实测抓取的 v1 tick：o/h/l/cV/cP/cA/quotes/t → v2 帧形"""
    v1 = {
        "t": "2026-08-20 22:14:41",
        "s": "RB2701",
        "o": 3069,
        "h": 3079,
        "l": 3069,
        "p": 3076,
        "cV": 63658,
        "cA": 1957412630,
        "cP": 766453,
        "tt": 8,
        "quotes": [{"aP": 3077, "aV": 246, "bP": 3076, "bV": 174}],
    }
    out = translate_v1_frame(v1)
    assert out["open"] == 3069 and out["high"] == 3079 and out["low"] == 3069
    assert out["total_vol"] == 63658 and out["open_int"] == 766453
    assert out["turnover"] == 1957412630
    assert out["q"] == v1["quotes"]  # 盘口数组换名、元素原样
    assert out["t"] == "20260820221441000"  # 17 位 v2 时间
    assert out["s"] == "RB2701" and out["p"] == 3076
    assert "o" not in out and "cV" not in out and "quotes" not in out
    assert v1["o"] == 3069  # 入参不被修改


def test_translate_v1_frame_control_passthrough():
    assert translate_v1_frame({"type": "welcome", "data": "ok"}) == {
        "type": "welcome",
        "data": "ok",
    }


async def test_on_frame_translates_in_v1_mode_and_passes_in_v2():
    hub = DownstreamHub()
    stats = RelayStats()
    mgr = UpstreamManager(
        RelaySettings(api_key="k"), hub, stats, RateTracker(), gate=PeerGate("", skip=True)
    )
    client = hub.register()
    tick = json.dumps(
        {"t": "2026-08-20 22:14:41", "s": "rb2701", "o": 3069, "h": 3079, "l": 3069,
         "p": 3076, "cV": 63658, "cP": 766453, "cA": 1957412630,
         "quotes": [{"aP": 3077, "aV": 246, "bP": 3076, "bV": 174}]}
    )
    raw = json.dumps([tick])  # v1 双重 JSON：外层数组元素是字符串
    # v1 上游：剥层后翻成 v2 帧形对象数组广播
    mgr.mode = "ws_v1"
    await mgr._on_frame(raw)
    frame = json.loads(client.queue.get_nowait())[0]
    assert frame["open"] == 3069 and frame["total_vol"] == 63658 and frame["q"][0]["bP"] == 3076
    # v2 上游：原样透传
    mgr.mode = "ws_v2"
    await mgr._on_frame(raw)
    assert client.queue.get_nowait() == raw


# ---------- apiKey 替换 / 缓存键 / 脱敏 ----------


def test_strip_api_key_removes_all_case_variants():
    params = {"apiKey": "secret1", "apikey": "secret2", "APIKEY": "secret3", "symbols": "rb2701"}
    stripped = strip_api_key(params)
    assert stripped == {"symbols": "rb2701"}
    assert "secret" not in json.dumps(stripped)


def test_cache_key_ignores_apikey_and_normalizes_order():
    a = cache_key("snapshot", {"symbols": "rb2701", "type": "14"})
    b = cache_key("snapshot", {"type": "14", "symbols": "rb2701"})
    c = cache_key("snapshot", {"symbols": "hc2701", "type": "14"})
    assert a == b  # 参数排序 → 顺序无关
    assert a != c
    # 不同下游 apiKey（已剥）不分裂缓存
    assert cache_key("snapshot", strip_api_key({"apiKey": "k1", "symbols": "rb2701"})) == \
        cache_key("snapshot", strip_api_key({"apiKey": "k2", "symbols": "rb2701"}))


def test_mask_keeps_no_secret():
    masked = _mask({"apiKey": "abcdef123456"})
    assert masked["apiKey"] == "abc***"
    assert "abcdef123456" not in json.dumps(masked)


def test_welcome_frame_shape():
    frame = build_welcome_frame(now=1771600000.9)
    assert frame == {"type": "welcome", "data": "连接成功 (relay)", "time": 1771600000}


# ---------- 订阅清单 ----------


def test_build_subscription_intersects_and_caps_100():
    masters = [f"rb{2600 + i:02d}" for i in range(60)] + [f"hc{2600 + i:02d}" for i in range(60)]
    valid = {str(s).upper() for s in masters if not str(s).endswith("00")}  # 剔除 *00 结尾
    valid.add("RB9999")  # 合成主力不应进入订阅
    out = build_subscription(masters, sorted(valid))
    assert len(out) == 100  # cap 100
    assert all(s == s.lower() for s in out)
    assert "rb2600" not in out  # 不在有效清单 → 剔除
    assert "rb9999" not in out  # 合成主力 → 剔除
    assert len(set(out)) == len(out)


def test_build_subscription_falls_back_when_valid_empty():
    out = build_subscription(["RB2701", "rb2701", "hc2701"], [])
    assert out == ["rb2701", "hc2701"]


def test_initial_masters_from_baked_table():
    masters = initial_masters()
    assert len(masters) > 60  # 烘焙基线 69 主力
    assert all(s == s.lower() for s in masters.values())
    assert masters.get("RB") == "rb2701"


def test_extra_watch_symbols_beside_masters():
    """附加观察合约：合约表非主力行进上游订阅，但不占品种键位"""
    masters = initial_masters()
    extras = extra_watch_symbols()
    assert masters.get("M") == "m2701"  # 2026-09-02 纠偏：真实主力 m2701
    assert extras == ["m2705"]
    assert not (set(extras) & set(masters.values()))


async def test_current_subscription_includes_extras():
    """上游订阅 = 主力基线 + 附加观察合约（有效清单过滤后仍含 m2705）"""
    mgr = UpstreamManager(
        RelaySettings(api_key="k"), DownstreamHub(), RelayStats(), RateTracker(),
        gate=PeerGate("", skip=True),
    )

    async def fake_valid():
        return ["RB2701", "M2705", "M2701"]

    mgr._valid_symbols = fake_valid
    symbols = await mgr._current_subscription()
    assert "m2701" in symbols and "m2705" in symbols and "rb2701" in symbols
    # 主力刷新（12h）只动 masters，附加清单不受影响
    updated = apply_master_refresh(mgr._masters, {"M": "m2701"})
    mgr._masters = updated
    symbols2 = await mgr._current_subscription()
    assert symbols2.count("m2701") == 1  # masters 并入后与 extras 去重


def test_apply_master_refresh_forward_only():
    """只进不退：VVTR 滞后月份挡住、前进放行、新品种补入

    2026-09-02 事故回归：VVTR 9999 整体滞后平台基线一代（返回
    rb2610 等旧主力），双向跟随会把订阅清单倒退一代。
    """
    current = {"RB": "rb2701", "CU": "cu2610"}
    updated = apply_master_refresh(current, {"RB": "rb2605", "PP": "pp2701"})
    assert updated["RB"] == "rb2701"  # 滞后月份挡住
    assert updated["CU"] == "cu2610"
    assert updated["PP"] == "pp2701"
    updated = apply_master_refresh(current, {"RB": "rb2705"})
    assert updated["RB"] == "rb2705"
    assert current["RB"] == "rb2701"  # 入参不被修改


# ---------- TTL 缓存 ----------


def test_ttl_cache_expiry_with_fake_clock():
    clock = {"t": 0.0}

    def now() -> float:
        return clock["t"]

    cache = TTLCache(maxsize=3, clock=now)
    resp = relay.CachedResponse(200, "application/json", b'{"code":200}')
    cache.put("a", resp, ttl=1.0)
    assert cache.get("a") is resp  # 命中
    clock["t"] = 1.5
    assert cache.get("a") is None  # 过期
    assert len(cache) == 0


def test_ttl_cache_lru_eviction():
    clock = {"t": 0.0}

    def now() -> float:
        return clock["t"]

    cache = TTLCache(maxsize=2, clock=now)
    resp = relay.CachedResponse(200, "application/json", b"x")
    cache.put("a", resp, ttl=100)
    cache.put("b", resp, ttl=100)
    cache.get("a")  # a 变最新，b 最旧
    cache.put("c", resp, ttl=100)  # 超容量 → 淘汰 b
    assert cache.get("b") is None
    assert cache.get("a") is resp
    assert cache.get("c") is resp


# ---------- 预检门控 ----------


class FakePeerRedis:
    def __init__(self) -> None:
        self.alive = True

    async def get(self, key: str):
        return '{"ts":1}' if self.alive else None


async def test_peer_gate_blocks_until_heartbeat_gone():
    fake = FakePeerRedis()
    gate = PeerGate(redis_url="redis://fake", skip=False, client=fake, interval=0.05)
    task = asyncio.create_task(gate.wait_until_clear())
    await asyncio.sleep(0.15)
    assert not task.done()  # 心跳键存在 → 一直不放行
    fake.alive = False  # 键消失（TTL 过期）
    await asyncio.wait_for(task, timeout=1)


async def test_peer_gate_skip_passes_immediately():
    gate = PeerGate(redis_url="redis://fake", skip=True, client=FakePeerRedis())
    await asyncio.wait_for(gate.wait_until_clear(), timeout=0.5)


async def test_peer_gate_redis_error_treated_as_alive():
    class BrokenRedis:
        async def get(self, key: str):
            raise ConnectionError("down")

    gate = PeerGate(redis_url="redis://fake", skip=False, client=BrokenRedis(), interval=0.05)
    task = asyncio.create_task(gate.wait_until_clear())
    await asyncio.sleep(0.12)
    assert not task.done()  # 无法确认 → 保守不放行
    task.cancel()


async def test_peer_gate_idle_bridge_passes():
    """停源后桥休眠仍写心跳（mode=idle 未连接）——不占上游，应放行"""

    class IdleRedis:
        async def get(self, key: str):
            return '{"ts":1,"channel":"vvtr","mode":"idle","connected":false,"symbols":0}'

    gate = PeerGate(redis_url="redis://fake", skip=False, client=IdleRedis())
    await asyncio.wait_for(gate.wait_until_clear(), timeout=0.5)


async def test_peer_gate_active_mode_blocks():
    class ActiveRedis:
        async def get(self, key: str):
            return '{"mode":"ws_v1","connected":true}'

    gate = PeerGate(
        redis_url="redis://fake", skip=False, client=ActiveRedis(), interval=0.05
    )
    task = asyncio.create_task(gate.wait_until_clear())
    await asyncio.sleep(0.12)
    assert not task.done()  # 非 idle（实际占用上游）→ 拦截
    task.cancel()


# ---------- 上游版本选择（套餐暂仅 v1：RELAY_WS_VERSION=1 不探测 v2） ----------


def test_next_version_forced_v1_and_auto():
    def make(ws_version: str = "auto") -> UpstreamManager:
        return UpstreamManager(
            RelaySettings(api_key="k", ws_version=ws_version),
            DownstreamHub(),
            RelayStats(),
            RateTracker(),
            gate=PeerGate("", skip=True),
        )

    assert make("1")._next_version() == 1  # 固定 v1，不看冷却
    mgr = make()
    assert mgr._next_version() == 2  # auto：冷却已过 → 先试 v2
    mgr._v2_cooldown_until = time.monotonic() + 600
    assert mgr._next_version() == 1  # auto：冷却期内 → v1


# ---------- 帧透传广播 ----------


async def test_upstream_frame_passthrough_broadcast_and_stats():
    hub = DownstreamHub()
    stats = RelayStats()
    mgr = UpstreamManager(
        RelaySettings(api_key="k"), hub, stats, RateTracker(), gate=PeerGate("", skip=True)
    )
    client1 = hub.register()
    client2 = hub.register()
    raw = '["{\\"s\\":\\"rb2701\\",\\"p\\":3500.5,\\"q\\":[]}"]'  # v1 双重 JSON
    await mgr._on_frame(raw)
    for c in (client1, client2):
        assert c.queue.qsize() == 1
        assert c.queue.get_nowait() == raw  # 原样透传，不剥层
    assert stats.frames_upstream == 1
    assert stats.frames_broadcast == 2
    assert mgr.upstream_symbols == 1  # 剥层仅用于统计
    await mgr._on_frame("pong")
    assert stats.frames_upstream == 1  # pong 不计


async def test_hub_broadcast_drops_oldest_for_slow_client():
    hub = DownstreamHub(queue_size=2)
    client = hub.register()
    for i in range(3):
        hub.broadcast(f"frame-{i}")
    assert client.queue.qsize() == 2
    assert client.queue.get_nowait() == "frame-1"  # 最旧被挤掉
    assert client.queue.get_nowait() == "frame-2"


# ---------- HTTP 面（假上游，无网无节流） ----------


def _client_app(monkeypatch, seen: list):
    settings = RelaySettings(api_key="REALKEY")

    def fake_forward(settings_, sub, params, rate_):
        seen.append((sub, dict(params)))
        return relay.CachedResponse(200, "application/json", b'{"code":200,"data":[]}')

    monkeypatch.setattr(relay, "_forward_sync", fake_forward)
    app = create_app(settings, start_upstream=False)
    return app


def test_http_proxy_replaces_apikey_and_shares_cache(monkeypatch):
    seen: list = []
    with TestClient(_client_app(monkeypatch, seen)) as client:
        r1 = client.get("/v1/snapshot?symbols=rb2701&apiKey=DOWN1")
        r2 = client.get("/snapshot?symbols=rb2701&apiKey=DOWN2")  # 无前缀 + 不同 key
        r3 = client.get("/v1/snapshot?symbols=hc2701&apiKey=DOWN3")
        health = client.get("/health").json()
        stats = client.get("/stats").json()
    assert r1.status_code == 200 and r1.json()["code"] == 200
    assert r2.status_code == 200 and r3.status_code == 200
    assert len(seen) == 2  # r2 命中缓存（键去 apiKey、路径归一）
    assert all("apiKey" not in p for _, p in seen)  # 转发前已剥下游 key
    assert seen[0][0] == "snapshot"
    assert stats["http_forwarded"] == 2 and stats["cache_hits"] == 1
    assert health["upstream_connected"] is False
    assert set(health) == {
        "upstream_connected",
        "upstream_mode",
        "downstream_clients",
        "upstream_symbols",
        "cache_size",
        "throttled_rps",
    }
    assert health["cache_size"] == 2  # rb2701 + hc2701


def test_http_proxy_does_not_cache_error_body(monkeypatch):
    seen: list = []

    settings = RelaySettings(api_key="REALKEY")

    def fake_forward(settings_, sub, params, rate_):
        seen.append(sub)
        return relay.CachedResponse(200, "application/json", b'{"code":429,"msg":"too many"}')

    monkeypatch.setattr(relay, "_forward_sync", fake_forward)
    app = create_app(settings, start_upstream=False)
    with TestClient(app) as client:
        client.get("/v1/symbols?apiKey=x")
        client.get("/v1/symbols?apiKey=y")
        stats = client.get("/stats").json()
    assert len(seen) == 2  # 业务错误不进缓存
    assert stats["upstream_429"] == 2


def test_http_proxy_real_symbol_long_cache_and_fresh(monkeypatch):
    """realSymbol 6h 长缓存：同参只出网一次；fresh=1 穿透重取并回写"""
    seen: list = []

    def fake_forward(settings_, sub, params, rate_):
        seen.append(dict(params))
        return relay.CachedResponse(
            200, "application/json", b'{"code":200,"data":[{"rsymbol":"DCE.m2701"}]}'
        )

    monkeypatch.setattr(relay, "_forward_sync", fake_forward)
    app = create_app(RelaySettings(api_key="REALKEY"), start_upstream=False)
    with TestClient(app) as client:
        q = "symbol=m9999&from=2026-09-01&to=2026-09-02"
        r1 = client.get(f"/v1/realSymbol?{q}&apiKey=D")  # 出网 + 入缓存
        r2 = client.get(f"/v1/realSymbol?{q}&apiKey=D")  # 命中缓存
        r3 = client.get(f"/v1/realSymbol?{q}&fresh=1&apiKey=D")  # 穿透重取
        r4 = client.get(f"/v1/realSymbol?{q}&apiKey=D")  # 穿透回写后再命中
        stats = client.get("/stats").json()
    assert r1.json()["data"] == [{"rsymbol": "DCE.m2701"}]
    assert r3.json()["data"] == [{"rsymbol": "DCE.m2701"}]
    assert len(seen) == 2  # 仅首次 + fresh 各出网一次
    assert all("fresh" not in p for p in seen)  # fresh 不进缓存键、不转发上游
    assert stats["cache_hits"] == 2  # r2 + r4


async def test_internal_refresh_shares_cache_and_paces(monkeypatch):
    """内部主力刷新走共键缓存：出网一次后命中；429 防护窗内中止扫表"""
    seen: list = []
    monkeypatch.setattr(relay, "_REAL_SYMBOL_SWEEP_GAP", 0.0)  # 测试不等待

    def fake_forward(settings_, sub, params, rate_):
        seen.append(dict(params))
        return relay.CachedResponse(
            200, "application/json", b'{"code":200,"data":[{"rsymbol":"DCE.m2701"}]}'
        )

    monkeypatch.setattr(relay, "_forward_sync", fake_forward)
    cache = TTLCache()
    pacing = PacingWindow()
    mgr = UpstreamManager(
        RelaySettings(api_key="k"), DownstreamHub(), RelayStats(), RateTracker(),
        gate=PeerGate("", skip=True), cache=cache, pacing=pacing,
    )
    # 首取：出网 + 解析（剥交易所前缀）+ 写共享缓存
    assert await mgr._cached_real_symbol("m") == "m2701"
    assert len(seen) == 1
    # 再取：命中缓存零出网（与下游代理共享条目——同键）
    assert await mgr._cached_real_symbol("m") == "m2701"
    assert len(seen) == 1
    # 429 防护窗激活：不出网，本轮返回空
    for _ in range(3):
        pacing.note_429()
    assert pacing.active() is True
    assert await mgr._cached_real_symbol("rb") == ""
    assert len(seen) == 1
    # 防护窗内整轮扫表立即中止：masters 不变、零出网
    before = dict(mgr._masters)
    assert await mgr._refresh_masters() is False
    assert mgr._masters == before
    assert len(seen) == 1


def test_rate_tracker_window():
    rate = RateTracker(window=60.0)
    rate.note()
    rate.note()
    assert 0 < rate.rps() <= 1.0


# ---------- 下游鉴权（RELAY_TOKEN） ----------


def test_authorized_allows_when_no_token_configured():
    """未配置 RELAY_TOKEN：保持 Phase 1 内网信任（放行）"""
    s = relay.RelaySettings(api_key="REALKEY", token="")
    assert relay.authorized(s, "") is True
    assert relay.authorized(s, "anything") is True


def test_authorized_requires_token_or_real_key():
    s = relay.RelaySettings(api_key="REALKEY", token="TOKEN")
    assert relay.authorized(s, "TOKEN") is True
    assert relay.authorized(s, "REALKEY") is True  # 过渡期：真钥也认
    assert relay.authorized(s, "WRONG") is False
    assert relay.authorized(s, "") is False


def test_http_proxy_rejects_bad_token(monkeypatch):
    seen: list = []

    def fake_forward(settings_, sub, params, rate_):
        seen.append(sub)
        return relay.CachedResponse(200, "application/json", b'{"code":200}')

    monkeypatch.setattr(relay, "_forward_sync", fake_forward)
    settings = relay.RelaySettings(api_key="REALKEY", token="TOKEN")
    app = relay.create_app(settings, start_upstream=False)
    with TestClient(app) as client:
        bad = client.get("/v1/snapshot?symbols=rb2701&apiKey=WRONG")
        no_key = client.get("/v1/snapshot?symbols=rb2701")
        ok = client.get("/v1/snapshot?symbols=rb2701&apiKey=TOKEN")
    assert bad.status_code == 401
    assert no_key.status_code == 401
    assert ok.status_code == 200
    assert seen == ["snapshot"]  # 仅放行的那次到达转发
