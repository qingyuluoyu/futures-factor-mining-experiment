"""VVTR 主力合约同步测试 —— 换月刷新 / 快照传播 / realSymbol 解析"""

import json

import pytest

import app.data.contracts as cc
from app.data.contracts import apply_active_contracts, rebuild_lookups
from app.services import contracts_sync


@pytest.fixture(autouse=True)
def _restore_static_table():
    """测试改的是模块级合约表，用例结束后恢复静态原样"""
    original = [dict(c) for c in cc.ALL_CONTRACTS]
    yield
    cc.ALL_CONTRACTS[:] = original
    rebuild_lookups()
    contracts_sync.reset_process_state()


# ---------- contracts.apply_active_contracts ----------


def test_apply_replaces_month_and_rebuilds_lookups():
    assert "pp2705" not in {c["symbol"] for c in cc.ALL_CONTRACTS}
    changed = apply_active_contracts(
        [
            {"symbol": "PP2705", "code": "pp"},  # 大小写应被规整
            {"symbol": "cu2611", "code": "CU"},
        ]
    )
    assert changed == 2
    syms = {c["symbol"] for c in cc.ALL_CONTRACTS}
    assert "pp2705" in syms and "pp2701" not in syms
    # 派生表同步重建
    assert cc.get_code_for_symbol("pp2705") == "PP"
    assert cc.get_contract_name("pp2705") == "聚丙烯2705"
    assert cc.get_master_symbol_for_code("pp") == "pp2705"
    assert cc.get_master_symbol_for_code("PP888") == "pp2705"
    # 保留静态人工维护的交易所
    pp = next(c for c in cc.ALL_CONTRACTS if c["code"] == "PP")
    assert pp["exchange"] == "大商所"


def test_apply_appends_new_product_and_keeps_missing():
    """VVTR 独有新品种追加；未返回的品种保留静态条目"""
    before = {c["symbol"] for c in cc.ALL_CONTRACTS}
    assert "zz2701" not in before  # 静态表确无此品种
    changed = apply_active_contracts(
        [{"symbol": "zz2701", "code": "ZZ", "name": "新品种2701", "exchange": "测试所"}]
    )
    assert changed == 1
    syms = {c["symbol"] for c in cc.ALL_CONTRACTS}
    assert "zz2701" in syms
    # 未提到的品种原样保留
    assert (syms - {"zz2701"}) == before


def test_apply_idempotent():
    apply_active_contracts([{"symbol": "pp2701", "code": "PP"}])
    assert apply_active_contracts([{"symbol": "pp2701", "code": "PP"}]) == 0
    assert apply_active_contracts([]) == 0


# ---------- 附加观察合约行（同品种第二行，非主力） ----------


def _m_symbols() -> list[str]:
    return [c["symbol"] for c in cc.ALL_CONTRACTS if c["code"] == "M"]


def test_static_extra_watch_row_master_unchanged():
    """静态表 M 双行：首行 m2701 是主力（2026-09-02 纠偏），m2705 仅附加"""
    syms = _m_symbols()
    assert syms[0] == "m2701"
    assert "m2705" in syms
    assert cc.get_master_symbol_for_code("m") == "m2701"
    assert cc.get_code_for_symbol("m2705") == "M"
    assert cc.get_contract_name("m2705") == "豆粕2705"


def test_apply_keeps_extra_row_when_master_rolls_on():
    """主力行换月只改首行，附加观察行不被冲成主力月份的重复行"""
    changed = apply_active_contracts([{"symbol": "m2709", "code": "M"}])
    assert changed == 1
    assert _m_symbols() == ["m2709", "m2705"]
    assert cc.get_master_symbol_for_code("M") == "m2709"
    # 幂等：重复套用同目标无变化
    assert apply_active_contracts([{"symbol": "m2709", "code": "M"}]) == 0


def test_apply_dedupes_when_master_lands_on_extra_month():
    """主力行换到附加行同月份 → 两行互换（旧主力月保留为附加行，不丢）

    2026-09-02 M 纠偏回归：表 [m2705, m2701]（过冲态）、目标 m2701 →
    [m2701, m2705]——m2705 必须仍在表内（订阅/下拉可见），而非被去重
    掉只剩 m2701。
    """
    # 模拟 58 当前过冲态：m2705 首行（主力）、m2701 附加行
    cc.ALL_CONTRACTS[:] = [
        {"symbol": "m2705", "code": "M", "name": "豆粕2705", "exchange": "大商所"},
        {"symbol": "m2701", "code": "M", "name": "豆粕2701", "exchange": "大商所"},
    ] + [c for c in cc.ALL_CONTRACTS if c["code"] != "M"]
    rebuild_lookups()
    changed = apply_active_contracts([{"symbol": "m2701", "code": "M"}])
    assert changed == 2  # 首行替换 + 附加行换回旧主力月
    assert _m_symbols() == ["m2701", "m2705"]
    assert cc.get_master_symbol_for_code("M") == "m2701"
    assert cc.get_contract_name("m2705") == "豆粕2705"


@pytest.mark.asyncio
async def test_resolve_targets_anchors_on_master_row(monkeypatch):
    """附加行不当锚点；VVTR 旧月返回被只进不退挡住（表不过冲时无变化）

    2026-09-02 事故回归：VVTR 9999 实测整体滞后平台基线一代（如返回
    m2609/rb2610 旧主力）——常规同步绝不能跟随回移，否则全表倒退。
    """
    cs = _fake_vvtr_config(monkeypatch, {("m", "9999"): "m2609"})
    targets = await cs._resolve_targets(None, object(), ("9999",))
    assert "M" not in targets  # 滞后月份被 later_symbol 挡住
    assert cc.get_master_symbol_for_code("M") == "m2701"


# ---------- contracts_sync 快照传播 ----------


class _FakeRedis:
    def __init__(self, snapshot: dict | None):
        self._snapshot = snapshot

    async def get(self, key):
        if self._snapshot is None:
            return None
        return json.dumps(self._snapshot, ensure_ascii=False)

    async def set(self, key, value, ex=None):
        self._snapshot = json.loads(value)
        return True


@pytest.mark.asyncio
async def test_ensure_applies_snapshot():
    contracts_sync.reset_process_state()
    snap = {
        "ts": 1_700_000_000.0,
        "entries": [{"symbol": "pp2701", "code": "PP"}],
    }
    await contracts_sync.ensure_active_contracts(_FakeRedis(snap))
    assert cc.get_master_symbol_for_code("pp") == "pp2701"
    # 重复套用同版本快照为空操作
    assert contracts_sync._state["applied_ts"] == snap["ts"]


@pytest.mark.asyncio
async def test_ensure_no_snapshot_keeps_static():
    contracts_sync.reset_process_state()
    await contracts_sync.ensure_active_contracts(_FakeRedis(None))
    # 无快照时保持静态基线（2026-08-20 烘焙：pp 主力 pp2701）
    assert "pp2701" in {c["symbol"] for c in cc.ALL_CONTRACTS}


# ---------- 月份只进不退 / 次主力迁移 ----------


def test_later_symbol_rules():
    from app.services.contracts_sync import later_symbol

    assert later_symbol("pp2609", "pp2701") == "pp2701"  # 前进
    assert later_symbol("pp2701", "pp2609") == "pp2701"  # 防回退
    assert later_symbol("pp2701", "pp2701") == "pp2701"  # 相等取 incoming
    assert later_symbol("pp2701", "") == "pp2701"  # VVTR 空保留现值
    assert later_symbol("", "pp2701") == "pp2701"  # 无现值取 incoming


@pytest.mark.asyncio
async def test_migrate_jumps_to_secondary(monkeypatch):
    """迁移：表 2609 → VVTR 次主力 2701 前进；2612（股指季月）同样前进"""
    from app.services import contracts_sync as cs

    calls = []

    async def fake_resolve(redis_conn, session_factory, suffixes):
        calls.append(suffixes)
        return {"PP": "pp2701", "IF": "if2612"}

    fake_redis = _FakeRedis(None)
    monkeypatch.setattr(cs, "_resolve_targets", fake_resolve)
    targets = await cs.migrate_to_next_contracts(fake_redis, None)
    assert targets == {"PP": "pp2701", "IF": "if2612"}
    assert calls == [("9999", "9998")]
    assert cc.get_master_symbol_for_code("pp") == "pp2701"
    assert cc.get_master_symbol_for_code("if") == "if2612"
    assert cc.get_contract_name("pp2701") == "聚丙烯2701"
    assert "2612" in cc.get_contract_name("if2612")
    # 快照必须是全量表（新进程以静态表为底套用，部分快照会让
    # 未包含品种回落旧月份）
    snap_entries = fake_redis._snapshot["entries"]
    assert len(snap_entries) == len(cc.ALL_CONTRACTS)
    assert {e["symbol"] for e in snap_entries} == {
        c["symbol"] for c in cc.ALL_CONTRACTS
    }


def _fake_vvtr_config(monkeypatch, fetch_map):
    """打桩：vvtr 配置（恒有效）+ realSymbol 按 (品种, 后缀) 返回"""
    from app.connectors.vvtr import http_client
    from app.services import contracts_sync as cs
    import app.connectors.vvtr.config as vvtr_cfg

    class _Cfg:
        api_key = "k"
        base_url = ""

    async def fake_cfg(sf):
        return _Cfg()

    def fake_fetch(api_key, product, base_url="", suffix="9999"):
        return fetch_map.get((product, suffix), "")

    monkeypatch.setattr(vvtr_cfg, "get_vvtr_runtime_config", fake_cfg)
    monkeypatch.setattr(http_client, "fetch_vvtr_real_master", fake_fetch)
    return cs


@pytest.mark.asyncio
async def test_refresh_never_reverts_after_migration(monkeypatch):
    """迁移后常态同步（9999 主力仍 2609）不得把表拉回 2609"""
    cs = _fake_vvtr_config(monkeypatch, {("pp", "9999"): "pp2609"})
    cc.apply_active_contracts([{"symbol": "pp2701", "code": "PP"}])
    await cs.refresh_masters_from_vvtr(_FakeRedis(None), object())
    assert cc.get_master_symbol_for_code("pp") == "pp2701"


@pytest.mark.asyncio
async def test_resolve_targets_applies_later_rule(monkeypatch):
    cs = _fake_vvtr_config(
        monkeypatch,
        {("pp", "9999"): "pp2609", ("pp", "9998"): "pp2701",
         ("cu", "9999"): "cu2610", ("cu", "9998"): "cu2609"},
    )
    cc.apply_active_contracts(
        [{"symbol": "cu2610", "code": "CU"}, {"symbol": "pp2609", "code": "PP"}]
    )
    # 迁移式双后缀：pp 主力 2609 未换月 → 次主力 2701 前进；
    # cu 主力 2610 → 次主力 2609 是"往回"的月份，取最大保持 2610
    targets = await cs._resolve_targets(None, object(), ("9999", "9998"))
    assert targets.get("PP") == "pp2701"
    assert "CU" not in targets  # 2610 == 2610 无变化不产生目标


# ---------- 过半自动换月（roll_window_open） ----------


def _bj(y, m, d):
    from datetime import datetime, timedelta, timezone

    return datetime(y, m, d, tzinfo=timezone(timedelta(hours=8)))


def test_roll_window_open_rules():
    from app.services.contracts_sync import roll_window_open as w

    # 2609（9 月交割）→ 换月窗口 8 月 16 日起
    assert w("pp2609", _bj(2026, 8, 16)) is True
    assert w("pp2609", _bj(2026, 8, 15)) is False
    assert w("pp2609", _bj(2026, 7, 20)) is False
    # 2610（10 月交割）→ 9 月 16 日起
    assert w("cu2610", _bj(2026, 8, 20)) is False
    assert w("cu2610", _bj(2026, 9, 16)) is True
    # 2701（次年 1 月交割）→ 上年 12 月 16 日起（跨年）
    assert w("rb2701", _bj(2026, 12, 16)) is True
    assert w("rb2701", _bj(2026, 12, 15)) is False
    assert w("rb2701", _bj(2026, 8, 20)) is False
    # 已过交割月（迟到的换月）也算窗口内
    assert w("pp2609", _bj(2026, 9, 20)) is True
    # 无月份解析失败不触发
    assert w("rb", _bj(2026, 9, 20)) is False


@pytest.mark.asyncio
async def test_resolve_auto_rolls_in_window(monkeypatch):
    """换月窗口内常态同步自动追加 9998 前进；窗口外不查次主力"""
    cs = _fake_vvtr_config(
        monkeypatch,
        {
            ("pp", "9999"): "pp2609",
            ("pp", "9998"): "pp2701",
            ("rb", "9999"): "rb2701",
        },
    )
    cc.apply_active_contracts(
        [{"symbol": "pp2609", "code": "PP"}, {"symbol": "rb2701", "code": "RB"}]
    )
    # 2026-08-20：pp2609 在窗口内（8/16 起）→ 追加次主力跳 2701；
    # rb2701 窗口在 12 月 → 不查 9998
    targets = await cs._resolve_targets(
        None, object(), ("9999",), now=_bj(2026, 8, 20)
    )
    assert targets.get("PP") == "pp2701"
    assert "RB" not in targets  # rb2701 == 主力，无变化


@pytest.mark.asyncio
async def test_resolve_holds_when_secondary_lagging(monkeypatch):
    """窗口内但次主力尚未转正（=当前月或往回）：保持，下轮重试"""
    cs = _fake_vvtr_config(
        monkeypatch,
        {("if", "9999"): "if2609", ("if", "9998"): "if2609"},
    )
    cc.apply_active_contracts([{"symbol": "if2609", "code": "IF"}])
    targets = await cs._resolve_targets(
        None, object(), ("9999",), now=_bj(2026, 8, 20)
    )
    assert targets == {}  # 不回退也不前进，等待 VVTR 次主力转正


# ---------- http_client.fetch_vvtr_real_master ----------


def test_fetch_real_master_parses_latest(monkeypatch):
    from app.connectors.vvtr import http_client

    captured = {}

    def fake_request(base_url, path, params):
        captured["path"] = path
        captured["params"] = params
        return {
            "code": 200,
            "data": [
                {"date": "2026-08-19", "rsymbol": "DCE.pp2609"},
                {"date": "2026-08-20", "rsymbol": "DCE.pp2701"},
            ],
        }

    monkeypatch.setattr(http_client, "_request", fake_request)
    out = http_client.fetch_vvtr_real_master("key", "pp")
    assert out == "pp2701"
    assert captured["path"] == "/realSymbol"
    assert captured["params"]["symbol"] == "pp9999"
    assert captured["params"]["from"] and captured["params"]["to"]


def test_fetch_real_master_empty_on_failure(monkeypatch):
    from app.connectors.vvtr import http_client

    monkeypatch.setattr(http_client, "_request", lambda *a, **k: None)
    assert http_client.fetch_vvtr_real_master("key", "pp") == ""
    assert http_client.fetch_vvtr_real_master("", "pp") == ""
