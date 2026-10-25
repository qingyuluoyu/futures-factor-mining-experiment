"""X3 修复测试 —— 过期合约回退到同品种其它合约"""

from __future__ import annotations

from app.services.factor_mining.loader import _merge_minute_bars


def test_candidate_fallback_logic_shape():
    """_load_minute 的回退逻辑依赖 _tickdata_minute_bars 返回空列表语义——
    此处锁定合并层行为不变，回退路径由集成验证（33 部署后 cu 分钟挖掘
    bars 从 ~1025 恢复）"""
    sina = [
        {"time": "2026-08-14 09:00", "open": 100.0, "close": 100.5},
        {"time": "2026-08-14 09:15", "open": 100.5, "close": 101.0},
    ]
    td = [
        {"time": "2026-08-13 21:00", "open": 99.0, "close": 99.5},
        {"time": "2026-08-14 09:00", "open": 100.0, "close": 100.6},
    ]
    merged = _merge_minute_bars(sina, td, 10)
    assert [b["time"] for b in merged] == [
        "2026-08-13 21:00", "2026-08-14 09:00", "2026-08-14 09:15",
    ]
    # 同 time 新浪优先
    assert merged[1]["close"] == 100.5


async def _run_load_minute_with_stub(monkeypatch, td_map, fresh=None):
    """用桩替身驱动 _load_minute 的合约回退路径"""
    from app.services.factor_mining import loader as L

    async def fake_get_kline_history(redis, sym, tf, **kw):
        return [
            {"time": "2026-08-14 09:00", "open": 100.0, "close": 100.5},
            {"time": "2026-08-14 09:15", "open": 100.5, "close": 101.0},
        ]

    async def fake_td(contract, tf, limit):
        return td_map.get(contract, [])

    async def fake_fresh(product, tf, k=3, recent_days=30):
        return fresh or []

    monkeypatch.setattr(L, "get_kline_history", fake_get_kline_history)
    monkeypatch.setattr(L, "_tickdata_minute_bars", fake_td)
    monkeypatch.setattr(
        "app.data.tickdata_kline.freshest_contracts", fake_fresh
    )
    return await L._load_minute(None, None, "cu", "15m")


async def test_expired_contract_falls_back(monkeypatch):
    """首选合约过期（空）→ tickdata 最新合约候选被采用（静态清单 cu 只有
    一条 cu2607 且已过期——正是 R2 发现的实况，回退靠 freshest_contracts）"""
    td_bars = [{"time": "2026-08-13 21:00", "open": 99.0, "close": 99.5}] * 30
    bars, _, _ = await _run_load_minute_with_stub(
        monkeypatch, {"cu2609": td_bars}, fresh=["cu2609", "cu2608"]
    )
    assert len(bars) >= 30  # 深历史被并入（不再降级纯新浪 2 根）


async def test_no_fallback_data_degrades_to_sina(monkeypatch):
    """全部候选都空 → 降级新浪（原行为）"""
    bars, _, _ = await _run_load_minute_with_stub(monkeypatch, {}, fresh=[])
    assert len(bars) == 2
