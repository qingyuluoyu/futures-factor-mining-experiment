"""自选行情跟随主力换月测试 —— 旧月份 symbol 读时映射到当前主力

背景：全表迁移/自动换月后合约列表只剩当前主力（如 rb2701），而自选
表里存的还是旧月份（rb2610），前端「★ 自选」分组因交集为空整组消失。
"""

from datetime import datetime
from types import SimpleNamespace

import pytest

from app.services.watchlist import merge_quotes_to_watchlist, resolve_current_master


def test_resolve_follows_current_master():
    # 静态基线 2026-08-20：rb 主力 rb2701
    sym, name = resolve_current_master("rb2610")
    assert sym == "rb2701"
    assert name == "螺纹钢2701"
    # 大小写归一
    assert resolve_current_master("RB2610")[0] == "rb2701"
    # 已是当前主力 → 原样，名称空（调用方保留库中原名）
    sym, name = resolve_current_master("rb2701")
    assert sym == "rb2701" and name == ""
    # 未知品种原样返回
    assert resolve_current_master("zzz9999")[0] == "zzz9999"


class _FakeRedis:
    """仅 rb2701 有行情（当前主力的统一键）"""

    async def get(self, key):
        if key.endswith("rb2701"):
            return '{"last_price": 3021.0, "change": -7.0, "change_pct": -0.23}'
        return None


def _item(symbol: str, name: str):
    return SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        trading_mode="live",
        contract_symbol=symbol,
        contract_name=name,
        sort_order=0,
        group_name=None,
        created_at=datetime(2026, 8, 1, 10, 0, 0),
    )


@pytest.mark.asyncio
async def test_merge_remaps_old_month_to_master():
    """旧月份自选（rb2610）读时映射为 rb2701 并合并到新主力行情"""
    merged = await merge_quotes_to_watchlist(
        [_item("rb2610", "螺纹钢2610")], _FakeRedis()
    )
    assert merged[0]["contract_symbol"] == "rb2701"
    assert merged[0]["contract_name"] == "螺纹钢2701"
    assert merged[0]["last_price"] == 3021.0
    # id/排序/分组/创建时间保留（前端删除自选依赖 UUID）
    assert merged[0]["id"] == "00000000-0000-0000-0000-000000000001"
    assert merged[0]["sort_order"] == 0
