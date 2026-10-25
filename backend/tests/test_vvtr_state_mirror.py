"""VVTR 状态镜像刷新测试 —— 会话供数期间镜像不得过期

背景：v1/v2 会话供数时主循环阻塞在 await 会话任务，外层不再刷新
market:vvtr:state（TTL 300s）→ 过期后下游把 VVTR 视为关闭、统一
行情静默切回新浪。修复：镜像刷新挂到常驻心跳循环。
"""

import pytest

from app.connectors.vvtr import ws_bridge as wb
from app.connectors.vvtr.config import VVTR_STATE_KEY, VvtrRuntimeConfig


class _RedisRecorder:
    def __init__(self):
        self.writes: list[tuple[str, dict]] = []

    async def set(self, key, value, ex=None):
        import json

        self.writes.append((key, json.loads(value)))


@pytest.mark.asyncio
async def test_refresh_state_mirror_writes_alive_state(monkeypatch):
    r = _RedisRecorder()
    bridge = wb.VvtrWsBridge(redis_conn=r, session_factory=object())

    async def fake_cfg(sf, *, force=False):
        return VvtrRuntimeConfig(
            enabled=True, quotes_enabled=True, kline_enabled=True,
            ws_enabled=True, api_key="k",
        )

    monkeypatch.setattr(wb, "get_vvtr_runtime_config", fake_cfg)
    await bridge._refresh_state_mirror()

    keys = [k for k, _ in r.writes]
    assert VVTR_STATE_KEY in keys
    payload = next(v for k, v in r.writes if k == VVTR_STATE_KEY)
    assert payload["enabled"] is True
    assert payload["quotes_enabled"] is True
    assert payload["ws_enabled"] is True


@pytest.mark.asyncio
async def test_refresh_state_mirror_swallows_errors(monkeypatch):
    """配置读取异常时静默（心跳循环不得中断）"""
    r = _RedisRecorder()
    bridge = wb.VvtrWsBridge(redis_conn=r, session_factory=object())

    async def boom(sf, *, force=False):
        raise RuntimeError("db down")

    monkeypatch.setattr(wb, "get_vvtr_runtime_config", boom)
    await bridge._refresh_state_mirror()  # 不抛异常
    assert r.writes == []
