"""主力口径对齐测试 —— 平台主力表优先，成交量口径兜底

背景（2026-08-20 实测）：量口径（成交量排序）在换月过渡期给出
RB=rb2610 / CU=cu2609（旧主力量未衰减，rb2610 量 34 万 vs rb2701
9.6 万），而平台主力表（VVTR 迁移基线，商品列表/订阅/落库同源）
是 rb2701 / cu2610。identify_master_contracts 对齐后，by-code 合约
树、K线落库清单、信号宇宙、AI 元数据全链路统一跟平台表。
"""

import fnmatch
import json
from datetime import datetime, timezone, timedelta

from app.services.contract_master import (
    identify_master_contracts,
    resolve_master_for_code,
)

_BJ = timezone(timedelta(hours=8))


class FakeRedis:
    """仅支持 identify_master_contracts 用到的 scan_iter/get"""

    def __init__(self, data: dict[str, str]) -> None:
        self._data = data

    async def scan_iter(self, pattern: str):
        for key in self._data:
            if fnmatch.fnmatch(key, pattern):
                yield key

    async def get(self, key: str):
        return self._data.get(key)


def _monthly(symbol: str, volume: int) -> tuple[str, str]:
    return (
        f"market:monthly:quote:{symbol}",
        json.dumps({"symbol": symbol, "volume": volume}),
    )


# ---------- 纯函数：resolve_master_for_code ----------


def test_resolve_prefers_platform_table_over_volume():
    assert resolve_master_for_code("RB", "rb2610") == "rb2701"
    assert resolve_master_for_code("rb", None) == "rb2701"
    assert resolve_master_for_code("rb2610", "rb2610") == "rb2701"
    assert resolve_master_for_code("CU", "cu2609") == "cu2610"


def test_resolve_falls_back_to_volume_when_code_unknown():
    # 表无该品种：get_master_symbol_for_code 原样返回入参，必须识别并退回量口径
    assert resolve_master_for_code("zz", "zz2609") == "zz2609"
    assert resolve_master_for_code("zz", None) is None
    assert resolve_master_for_code("", "x2609") == "x2609"


# ---------- 集成：identify_master_contracts（假 Redis） ----------


async def test_identify_aligns_master_and_demotes_volume_master():
    """量口径 rb2610(34万手) > rb2701(9.6万手)，但主力必须是平台表 rb2701；
    旧量主力 rb2610 降为 secondary"""
    fake = FakeRedis(
        dict(
            [
                _monthly("rb2610", 344467),
                _monthly("rb2701", 96182),
                _monthly("rb2705", 10),
            ]
        )
    )
    result = await identify_master_contracts(fake, now=datetime(2026, 8, 20, tzinfo=_BJ))
    rb = result["RB"]
    assert rb["master"] == "rb2701"  # 平台表
    assert rb["secondary"] == "rb2610"  # 量最大的非主力（换月观察）


async def test_identify_platform_master_without_volume_data():
    """平台主力无行情数据（如 cu2610 缺全月行情）时仍是 master，
    量口径旧主力 cu2609 作 secondary——K线落库因此会跟进平台主力"""
    fake = FakeRedis(dict([_monthly("cu2609", 26071)]))
    result = await identify_master_contracts(fake, now=datetime(2026, 8, 20, tzinfo=_BJ))
    cu = result["CU"]
    assert cu["master"] == "cu2610"
    assert cu["secondary"] == "cu2609"


async def test_identify_unknown_code_keeps_volume_master():
    fake = FakeRedis(dict([_monthly("zz2609", 100)]))
    result = await identify_master_contracts(fake, now=datetime(2026, 8, 20, tzinfo=_BJ))
    assert result["ZZ"]["master"] == "zz2609"
    assert result["ZZ"]["secondary"] is None


async def test_identify_skips_zero_volume_codes():
    fake = FakeRedis(dict([_monthly("rb2610", 0)]))
    result = await identify_master_contracts(fake, now=datetime(2026, 8, 20, tzinfo=_BJ))
    assert "RB" not in result
