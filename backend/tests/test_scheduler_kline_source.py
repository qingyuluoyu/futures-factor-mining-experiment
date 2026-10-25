"""live K 线 forming 源选择测试 —— ctp_sim 渠道用桥源键驱动

覆盖 scheduler._live_kline_quotes 的 source_label 决策：
real_ctp / ctp_sim / vvtr（启用）→ 各自源键；其余渠道 → sina 源键。
"""

from __future__ import annotations

import fnmatch
import json
import time

import pytest

import app.services.scheduler as scheduler


class _ScanRedis:
    """最小 Redis 替身：get + scan_iter（_live_kline_quotes 只用这两个）"""

    def __init__(self, initial: dict[str, str] | None = None):
        self.data: dict[str, str] = dict(initial or {})

    async def get(self, key: str):
        return self.data.get(key)

    async def set(self, key: str, value, ex=None):
        self.data[key] = value

    async def scan_iter(self, pattern: str):
        for key in list(self.data):
            if fnmatch.fnmatch(key, pattern):
                yield key


def _quote(symbol: str, last: float, source: str) -> str:
    return json.dumps(
        {
            "symbol": symbol,
            "last_price": last,
            "source": source,
            "recv_ts": time.time(),
        }
    )


@pytest.mark.asyncio
async def test_ctp_sim_channel_reads_ctp_sim_source_keys():
    r = _ScanRedis(
        {
            "market:live:channel": "ctp_sim",
            "market:live:source:ctp_sim:quote:m2701": _quote("m2701", 2998.0, "ctp_sim"),
            "market:live:source:sina:quote:rb2701": _quote("rb2701", 3090.0, "sina"),
        }
    )
    quotes = await scheduler._live_kline_quotes(r)
    symbols = {q["symbol"] for q in quotes}
    # 只读 ctp_sim 源键（sina 源键不混入）
    assert symbols == {"m2701"}
    assert quotes[0]["source"] == "ctp_sim"


@pytest.mark.asyncio
async def test_default_channel_reads_sina_source_keys():
    r = _ScanRedis(
        {
            "market:live:channel": "sina",
            "market:live:source:sina:quote:rb2701": _quote("rb2701", 3090.0, "sina"),
            "market:live:source:ctp_sim:quote:m2701": _quote("m2701", 2998.0, "ctp_sim"),
        }
    )
    quotes = await scheduler._live_kline_quotes(r)
    symbols = {q["symbol"] for q in quotes}
    assert symbols == {"rb2701"}


@pytest.mark.asyncio
async def test_stale_source_key_filtered():
    """源键陈旧（recv_ts 超 90s 窗口）不喂 forming"""
    stale = json.dumps(
        {"symbol": "m2701", "last_price": 2998.0, "source": "ctp_sim", "recv_ts": time.time() - 600}
    )
    r = _ScanRedis(
        {
            "market:live:channel": "ctp_sim",
            "market:live:source:ctp_sim:quote:m2701": stale,
        }
    )
    quotes = await scheduler._live_kline_quotes(r)
    assert quotes == []
