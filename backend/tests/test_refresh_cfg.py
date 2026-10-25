"""刷新间隔运行时配置测试 —— 默认值 + per-channel 覆盖 + 钳位 + 旧格式兼容"""

from __future__ import annotations

import json

import pytest

from app.services import refresh_cfg


class _FakeRedis:
    """最小 Redis 假实现（异步 get/set）"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value


# ---------- _clamp ----------


def test_clamp_within_range() -> None:
    assert refresh_cfg._clamp(5) == 5
    assert refresh_cfg._clamp(1) == 1
    assert refresh_cfg._clamp(10) == 10


def test_clamp_below_min() -> None:
    assert refresh_cfg._clamp(0) == refresh_cfg.MIN_INTERVAL
    assert refresh_cfg._clamp(-3) == refresh_cfg.MIN_INTERVAL


def test_clamp_above_max() -> None:
    assert refresh_cfg._clamp(11) == refresh_cfg.MAX_INTERVAL
    assert refresh_cfg._clamp(9999) == refresh_cfg.MAX_INTERVAL


def test_clamp_invalid_type() -> None:
    assert refresh_cfg._clamp("abc") == refresh_cfg.DEFAULT_INTERVAL
    assert refresh_cfg._clamp(None) == refresh_cfg.DEFAULT_INTERVAL


# ---------- get_refresh_interval 默认 / miss ----------


@pytest.mark.asyncio
async def test_get_default_when_redis_none() -> None:
    assert await refresh_cfg.get_refresh_interval(None) == refresh_cfg.DEFAULT_INTERVAL
    assert await refresh_cfg.get_refresh_interval(None, "sina") == (
        refresh_cfg.DEFAULT_INTERVAL
    )


@pytest.mark.asyncio
async def test_get_default_when_miss() -> None:
    r = _FakeRedis()
    assert await refresh_cfg.get_refresh_interval(r) == refresh_cfg.DEFAULT_INTERVAL
    assert await refresh_cfg.get_refresh_interval(r, "sina") == (
        refresh_cfg.DEFAULT_INTERVAL
    )


# ---------- write + get（默认值）----------


@pytest.mark.asyncio
async def test_write_default_then_get() -> None:
    r = _FakeRedis()
    await refresh_cfg.write_refresh_intervals_to_redis(r, 3, None)
    # channel=None 用默认
    assert await refresh_cfg.get_refresh_interval(r) == 3
    # 未在 per-channel 的渠道也用默认
    assert await refresh_cfg.get_refresh_interval(r, "simnow") == 3


# ---------- per-channel 覆盖 ----------


@pytest.mark.asyncio
async def test_per_channel_overrides_default() -> None:
    r = _FakeRedis()
    await refresh_cfg.write_refresh_intervals_to_redis(r, 1, {"sina": 3})
    assert await refresh_cfg.get_refresh_interval(r, "sina") == 3
    # 其他渠道仍用默认
    assert await refresh_cfg.get_refresh_interval(r, "simnow") == 1


@pytest.mark.asyncio
async def test_unconfigured_channel_uses_default() -> None:
    r = _FakeRedis()
    await refresh_cfg.write_refresh_intervals_to_redis(
        r, 2, {"sina": 5, "real_ctp": 4}
    )
    assert await refresh_cfg.get_refresh_interval(r, "openctp_sim") == 2


@pytest.mark.asyncio
async def test_get_none_channel_uses_default_even_with_map() -> None:
    """channel=None 永远返回默认，不受 per-channel 影响"""
    r = _FakeRedis()
    await refresh_cfg.write_refresh_intervals_to_redis(r, 1, {"sina": 9})
    assert await refresh_cfg.get_refresh_interval(r, None) == 1


# ---------- 钳位 ----------


@pytest.mark.asyncio
async def test_write_clamps_default_and_channel() -> None:
    r = _FakeRedis()
    # default=99 → 10；sina=0 → 1
    await refresh_cfg.write_refresh_intervals_to_redis(r, 99, {"sina": 0})
    assert await refresh_cfg.get_refresh_interval(r) == refresh_cfg.MAX_INTERVAL
    assert await refresh_cfg.get_refresh_interval(r, "sina") == (
        refresh_cfg.MIN_INTERVAL
    )


@pytest.mark.asyncio
async def test_dirty_store_clamped() -> None:
    """Redis 被脏写（绕过 admin），get 仍钳位"""
    r = _FakeRedis()
    r.store[refresh_cfg.CONFIG_KEY] = json.dumps(
        {"default": 999, "sina": -5}
    )
    assert await refresh_cfg.get_refresh_interval(r) == refresh_cfg.MAX_INTERVAL
    assert await refresh_cfg.get_refresh_interval(r, "sina") == (
        refresh_cfg.MIN_INTERVAL
    )


# ---------- 旧格式兼容 ----------


@pytest.mark.asyncio
async def test_legacy_sec_format_treated_as_default() -> None:
    """旧版 {"sec": N} 视作默认值"""
    r = _FakeRedis()
    r.store[refresh_cfg.CONFIG_KEY] = json.dumps({"sec": 7})
    assert await refresh_cfg.get_refresh_interval(r) == 7
    assert await refresh_cfg.get_refresh_interval(r, "sina") == 7


# ---------- noop ----------


@pytest.mark.asyncio
async def test_write_none_redis_noop() -> None:
    await refresh_cfg.write_refresh_intervals_to_redis(None, 5, {"sina": 3})
    assert await refresh_cfg.get_refresh_interval(None) == (
        refresh_cfg.DEFAULT_INTERVAL
    )
