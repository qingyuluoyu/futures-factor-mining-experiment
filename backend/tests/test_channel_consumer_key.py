"""渠道消费键同步测试 —— 监控决策必须落到 market:live:channel

背景：健康监控只写记账键 market:live:active_channel，消费端读的是
market:live:channel（兼容旧 market:channel）。Redis 被部署重置后
消费键缺失 → 静默回落 sina（夜盘实测：WSS 1s 数据在源键里，前台
却拿新浪 3s）。
"""

import pytest

from app.services.channel_failover import sync_consumer_channel_key


class _FakeRedis:
    def __init__(self, initial: dict[str, str] | None = None):
        self.data: dict[str, str] = dict(initial or {})

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.data[key] = value


@pytest.mark.asyncio
async def test_sync_writes_consumer_key_when_missing():
    """Redis 重置后消费键缺失 → 同步为 active（vvtr）"""
    r = _FakeRedis()  # 空 Redis：无 market:live:channel / market:channel
    changed = await sync_consumer_channel_key(r, "vvtr")
    assert changed is True
    assert r.data["market:live:channel"] == "vvtr"
    assert r.data["market:channel"] == "vvtr"  # 兼容旧键


@pytest.mark.asyncio
async def test_sync_fixes_stale_consumer_key():
    """消费键停留在旧值（sina）→ 修正为决策渠道"""
    r = _FakeRedis({"market:live:channel": "sina", "market:channel": "sina"})
    assert await sync_consumer_channel_key(r, "vvtr") is True
    assert r.data["market:live:channel"] == "vvtr"


@pytest.mark.asyncio
async def test_sync_noop_when_consistent():
    r = _FakeRedis({"market:live:channel": "vvtr"})
    assert await sync_consumer_channel_key(r, "vvtr") is False
    assert r.data["market:live:channel"] == "vvtr"
