"""K 线多数据源调度测试 —— 主渠道 + 备用渠道降级 + 健康冷却 + 配置解析（无网络/DB）"""

from __future__ import annotations

import pytest

from app.data import kline_provider as kp


class _FakeRedis:
    """支持健康检查的最小 Redis 假实现（异步接口）"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def delete(self, *keys: str) -> None:
        for k in keys:
            self.store.pop(k, None)

    async def exists(self, key: str) -> int:
        return 1 if key in self.store else 0

    async def incr(self, key: str) -> int:
        val = int(self.store.get(key, 0)) + 1
        self.store[key] = str(val)
        return val

    async def expire(self, key: str, seconds: int) -> None:
        pass

    def pipeline(self) -> "_FakePipe":
        return _FakePipe(self)


class _FakePipe:
    def __init__(self, r: _FakeRedis) -> None:
        self.r = r
        self._ops: list[tuple[str, tuple]] = []

    def incr(self, key: str) -> "_FakePipe":
        self._ops.append(("incr", (key,)))
        return self

    def expire(self, key: str, seconds: int) -> "_FakePipe":
        self._ops.append(("expire", (key, seconds)))
        return self

    def delete(self, *keys: str) -> "_FakePipe":
        self._ops.append(("delete", keys))
        return self

    async def execute(self) -> list[int]:
        results: list[int] = []
        for op, args in self._ops:
            if op == "delete":
                await self.r.delete(*args)
                results.append(1)
            else:
                results.append(await getattr(self.r, op)(*args))
        return results


def _bar(time: str, close: float = 100.0) -> dict:
    return {
        "time": time,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 10,
        "settle": None,
        "open_interest": 100,
    }


def _install_fetchers(
    monkeypatch: pytest.MonkeyPatch,
    results: dict[str, list],
    calls: list[str] | None = None,
) -> None:
    """安装假源：results 为 {source: bars}，None 值表示抛异常"""

    def make(source: str):
        def fetch(symbol: str, period: str):
            if calls is not None:
                calls.append(source)
            res = results.get(source)
            if res is None:
                raise RuntimeError(f"{source} down")
            return list(res)

        return fetch

    # 禁用内部重建 fetchers（否则会覆盖注入的假源）
    monkeypatch.setattr(kp, "_register_fetchers", lambda *a, **k: None)
    monkeypatch.setattr(
        kp,
        "_SOURCE_FETCHERS",
        {s: make(s) for s in kp.VALID_KLINE_SOURCES},
    )


# ---------- 配置解析 ----------


def test_normalize_sources_filters_and_dedupes() -> None:
    assert kp.normalize_sources("sina,simnow,remote") == [
        "sina",
        "simnow",
        "remote",
    ]
    # 非法源被过滤、重复去重、保序（tencent/tqsdk 已移除，应被过滤）
    assert kp.normalize_sources("sina,tencent,sina,xxx,SIMNOW") == [
        "sina",
        "simnow",
    ]
    assert kp.normalize_sources("sina,tqsdk,eastmoney") == ["sina"]
    # 空配置回默认（主=新浪，备=simnow）
    assert kp.normalize_sources("") == ["sina", "simnow"]
    assert kp.normalize_sources(None) == ["sina", "simnow"]
    # 全非法回默认
    assert kp.normalize_sources("xxx,yyy") == ["sina", "simnow"]


def test_normalize_sources_includes_remote() -> None:
    assert "remote" in kp.normalize_sources("sina,simnow,remote")


def test_fetch_remote_parses_response() -> None:
    """远端源：从 /api/market/kline 响应解析标准 bar"""
    payload = {
        "symbol": "rb2610",
        "period": "1d",
        "bars": [
            {
                "time": "2026-08-07",
                "open": 3013.0,
                "high": 3025.0,
                "low": 3000.0,
                "close": 3008.0,
                "volume": 824109,
                "settle": None,
                "open_interest": 2254973,
            }
        ],
        "has_more": True,
    }

    class _Resp:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return payload

    from unittest.mock import patch

    with patch("httpx.get", return_value=_Resp()) as m:
        bars = kp._fetch_remote("rb2610", "1d", "http://192.0.2.33:8002")
    m.assert_called_once()
    assert len(bars) == 1
    assert bars[0]["time"] == "2026-08-07"
    assert bars[0]["close"] == 3008.0


def test_fetch_remote_no_base_returns_empty() -> None:
    assert kp._fetch_remote("rb2610", "1d", "") == []


# ---------- 主备降级 ----------


@pytest.mark.asyncio
async def test_primary_success_no_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """主渠道成功 → 只调用主渠道，不触发降级"""
    r = _FakeRedis()
    calls: list[str] = []
    _install_fetchers(
        monkeypatch,
        {
            "sina": [_bar("2026-08-07 10:00:00")],
            "simnow": [_bar("2026-08-07 10:00:00")],
        },
        calls,
    )
    bars = await kp.fetch_kline_with_fallback(
        r, "rb2610", "1d", primary="sina", backups=["simnow"]
    )
    assert calls == ["sina"]  # 主成功，备用未调用
    assert len(bars) == 1


@pytest.mark.asyncio
async def test_primary_fallback_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """主渠道失败 → 按备用顺序降级，返回第一个成功源的数据"""
    r = _FakeRedis()
    calls: list[str] = []
    _install_fetchers(
        monkeypatch,
        {"eastmoney": [], "sina": [], "simnow": [_bar("2026-08-07 10:00:00")]},
        calls,
    )
    bars = await kp.fetch_kline_with_fallback(
        r, "rb2610", "1d", primary="sina", backups=["simnow"]
    )
    assert calls == ["sina", "simnow"]
    assert len(bars) == 1
    assert bars[0]["time"] == "2026-08-07 10:00:00"


@pytest.mark.asyncio
async def test_all_fail_returns_empty_and_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """全部失败（异常）→ []，且每个源失败计数 +1

    2026-08-25 语义变更（openspec kline-source-failure-semantics）：
    空=合约无数据不再计失败；计数语义只覆盖异常路径。
    """
    r = _FakeRedis()
    _install_fetchers(monkeypatch, {"sina": None, "simnow": None})
    bars = await kp.fetch_kline_with_fallback(
        r, "rb2610", "1d", primary="sina", backups=["simnow"]
    )
    assert bars == []
    assert r.store[kp._fails_key("sina")] == "1"
    assert r.store[kp._fails_key("sina")] == "1"
    assert r.store[kp._fails_key("simnow")] == "1"


@pytest.mark.asyncio
async def test_failure_threshold_enters_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """连续失败（异常）达阈值 → 源进入冷却（键存在）"""
    r = _FakeRedis()
    _install_fetchers(monkeypatch, {"sina": None, "simnow": None})
    for _ in range(kp.FAIL_THRESHOLD):
        await kp.fetch_kline_with_fallback(
            r, "rb2610", "1d", primary="sina", backups=["simnow"]
        )
    assert await r.exists(kp._cooldown_key("sina")) == 1


@pytest.mark.asyncio
async def test_cooldown_primary_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """主渠道冷却中 → 跳过主渠道，直接尝试备用"""
    r = _FakeRedis()
    calls: list[str] = []
    _install_fetchers(
        monkeypatch,
        {"sina": [], "simnow": [_bar("2026-08-07 10:00:00")]},
        calls,
    )
    r.store[kp._cooldown_key("sina")] = "1"
    bars = await kp.fetch_kline_with_fallback(
        r, "rb2610", "1d", primary="sina", backups=["simnow"]
    )
    assert "sina" not in calls  # 主渠道被跳过未调用
    assert calls[0] == "simnow"
    assert len(bars) == 1


@pytest.mark.asyncio
async def test_success_clears_health(monkeypatch: pytest.MonkeyPatch) -> None:
    """源成功后失败计数与冷却清零"""
    r = _FakeRedis()
    # 只预置失败计数（若预置 cooldown 键会被冷却机制跳过，测不到成功路径）
    r.store[kp._fails_key("sina")] = "2"
    _install_fetchers(
        monkeypatch,
        {"eastmoney": [], "sina": [_bar("2026-08-07 10:00:00")], "simnow": []},
    )
    await kp.fetch_kline_with_fallback(
        r, "rb2610", "1d", primary="sina", backups=["simnow"]
    )
    assert kp._fails_key("simnow") not in r.store
    assert kp._cooldown_key("simnow") not in r.store


@pytest.mark.asyncio
async def test_primary_dedup_with_backup(monkeypatch: pytest.MonkeyPatch) -> None:
    """主渠道重复出现在 backups 中 → 去重，只调用一次"""
    r = _FakeRedis()
    calls: list[str] = []
    _install_fetchers(
        monkeypatch,
        {"eastmoney": [_bar("2026-08-07 10:00:00")], "sina": [], "tqsdk": []},
        calls,
    )
    await kp.fetch_kline_with_fallback(
        r,
        "rb2610",
        "1d",
        primary="eastmoney",
        backups=["eastmoney", "sina", "tqsdk"],  # eastmoney 重复
    )
    assert calls == ["sina"]  # 主成功即返回，且后续不会重复


# ---------- 默认主备（无参数） ----------


@pytest.mark.asyncio
async def test_default_primary_backup(monkeypatch: pytest.MonkeyPatch) -> None:
    """不传 primary/backups → 默认主=新浪, 备=simnow"""
    r = _FakeRedis()
    calls: list[str] = []
    _install_fetchers(
        monkeypatch,
        {
            "sina": [_bar("2026-08-07 10:00:00")],
            "simnow": [],
        },
        calls,
    )
    bars = await kp.fetch_kline_with_fallback(r, "rb2610", "1d")
    assert calls == ["sina"]  # 默认主渠道优先
    assert len(bars) == 1


# ---------- 无 Redis ----------


@pytest.mark.asyncio
async def test_without_redis_still_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无 Redis（AI 工具兜底路径）：不做健康限制，仍可降级"""
    calls: list[str] = []
    _install_fetchers(
        monkeypatch,
        {"eastmoney": [], "sina": [], "simnow": [_bar("2026-08-07 10:00:00")]},
        calls,
    )
    bars = await kp.fetch_kline_with_fallback(
        None, "rb2610", "1d", primary="sina", backups=["simnow"]
    )
    assert calls == ["sina", "simnow"]
    assert len(bars) == 1


# ---------- PG 兜底 ----------


class _FakePG:
    """模拟 PG 查询结果：按表名返回假行"""

    def __init__(self, daily: list[dict] | None = None, minute: list[dict] | None = None):
        self.daily = daily or []
        self.minute = minute or []


@pytest.mark.asyncio
async def test_fetch_from_pg_daily(monkeypatch: pytest.MonkeyPatch) -> None:
    """日线兜底：PG kline_history 返回标准 bar"""
    from app.services import kline as kline_svc

    rows = [
        {"time": "2026-07-29", "open": 100.0, "high": 105.0, "low": 99.0, "close": 103.0, "volume": 100},
        {"time": "2026-07-30", "open": 103.0, "high": 108.0, "low": 102.0, "close": 106.0, "volume": 120},
    ]
    pg = _FakePG(daily=rows)

    async def _q(session, code, end_date, limit):
        return pg.daily

    monkeypatch.setattr(kline_svc, "get_code_for_symbol", lambda s: "RB")
    monkeypatch.setattr("app.services.kline_history.query_history_bars", _q)
    bars = await kline_svc._fetch_from_pg(pg, "rb2610", "1d")
    assert len(bars) == 2
    assert bars[0]["time"] == "2026-07-29"
    assert bars[-1]["close"] == 106.0


@pytest.mark.asyncio
async def test_fetch_from_pg_minute_1m(monkeypatch: pytest.MonkeyPatch) -> None:
    """1m 兜底：直接返回 PG 分钟行"""
    from app.services import kline as kline_svc

    rows = [
        {"time": "2026-07-30 09:00:00", "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.2, "volume": 10},
        {"time": "2026-07-30 09:01:00", "open": 100.2, "high": 100.8, "low": 100.0, "close": 100.6, "volume": 12},
    ]
    pg = _FakePG(minute=rows)

    async def _q(session, code, period, end_time, limit):
        return pg.minute

    monkeypatch.setattr(kline_svc, "get_code_for_symbol", lambda s: "RB")
    monkeypatch.setattr("app.services.kline_minute.query_minute_history", _q)
    bars = await kline_svc._fetch_from_pg(pg, "rb2610", "1m")
    assert len(bars) == 2
    assert bars[0]["time"] == "2026-07-30 09:00:00"


@pytest.mark.asyncio
async def test_fetch_from_pg_minute_aggregate(monkeypatch: pytest.MonkeyPatch) -> None:
    """多周期兜底：PG 1m 聚合成 5m"""
    from app.services import kline as kline_svc

    rows = []
    for i in range(10):
        rows.append(
            {
                "time": f"2026-07-30 09:{i:02d}:00",
                "open": 100.0 + i,
                "high": 100.5 + i,
                "low": 99.5 + i,
                "close": 100.2 + i,
                "volume": 10,
            }
        )
    pg = _FakePG(minute=rows)

    async def _q(session, code, period, end_time, limit):
        return pg.minute

    monkeypatch.setattr(kline_svc, "get_code_for_symbol", lambda s: "RB")
    monkeypatch.setattr("app.services.kline_minute.query_minute_history", _q)
    bars = await kline_svc._fetch_from_pg(pg, "rb2610", "5m")
    assert len(bars) >= 1
    assert len(bars) < 10  # 聚合后根数减少


@pytest.mark.asyncio
async def test_fetch_from_pg_unknown_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    """未知合约 → 空（不查 PG）"""
    from app.services import kline as kline_svc

    monkeypatch.setattr(kline_svc, "get_code_for_symbol", lambda s: "")
    assert await kline_svc._fetch_from_pg(_FakePG(), "xx2609", "1d") == []
