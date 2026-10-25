"""VVTR 配置行解析单测 —— 兼容 SQLAlchemy RowMapping 与 asyncpg Record

历史 bug：_row_to_config 用 getattr 读行数据，asyncpg Record 不支持
属性访问导致全字段回落默认值（key 空 / mobile 空 → /my/permissions 400）。
"""

from app.connectors.vvtr.config import (
    _row_to_config,
    cap_symbol_list,
    match_valid_vvtr_symbols,
)


def test_match_valid_vvtr_symbols():
    """主力 ∩ VVTR 有效清单：大写归一、剔除下市合约、保序去重"""
    masters = ["rb2610", "AU2612", "ag2612", "cu2607", "rb2610", ""]
    valid = ["RB2610", "AU2612", "AG2612", "IC9999"]
    out = match_valid_vvtr_symbols(masters, valid)
    assert out == ["RB2610", "AU2612", "AG2612"]


def _row_dict() -> dict:
    return {
        "vvtr_enabled": True,
        "vvtr_quotes_enabled": True,
        "vvtr_ws_enabled": False,
        "vvtr_kline_enabled": True,
        "vvtr_api_key": "",
        "vvtr_mobile": "13800000000",
        "vvtr_base_url": "https://rest.vvtr.com/v1",
        "vvtr_ws_url": "wss://rest.vvtr.com/v2/connect",
    }


class _FakeAsyncpgRecord:
    """模拟 asyncpg Record：支持 dict()/下标访问，不支持属性访问"""

    def __init__(self, data: dict) -> None:
        self._data = dict(data)

    def keys(self):
        return self._data.keys()

    def __getitem__(self, key: str):
        return self._data[key]


def test_row_to_config_plain_dict():
    cfg = _row_to_config(_row_dict())
    assert cfg.enabled is True
    assert cfg.quotes_enabled is True
    assert cfg.ws_enabled is False
    assert cfg.kline_enabled is True
    assert cfg.mobile == "13800000000"
    assert cfg.base_url == "https://rest.vvtr.com/v1"
    assert cfg.ws_url == "wss://rest.vvtr.com/v2/connect"


def test_row_to_config_asyncpg_record_without_attr_access():
    record = _FakeAsyncpgRecord(_row_dict())
    # asyncpg Record 无属性访问 —— 修复前此处会全量回落默认值
    assert not hasattr(record, "vvtr_enabled")
    cfg = _row_to_config(record)
    assert cfg.enabled is True
    assert cfg.ws_enabled is False
    assert cfg.mobile == "13800000000"
    assert cfg.base_url == "https://rest.vvtr.com/v1"


def test_row_to_config_defaults_on_empty_row():
    cfg = _row_to_config(
        {
            "vvtr_enabled": False,
            "vvtr_quotes_enabled": True,
            "vvtr_kline_enabled": False,
            "vvtr_api_key": "",
            "vvtr_mobile": "",
            "vvtr_base_url": "",
            "vvtr_ws_url": "",
        }
    )
    assert cfg.enabled is False
    assert cfg.ws_active is False  # 无密钥 → 实时链路不可用
    assert cfg.base_url == "https://rest.vvtr.com/v1"
    assert cfg.ws_url == "wss://rest.vvtr.com/v2/connect"


def test_cap_symbol_list_filters_and_caps():
    """订阅清洗：去重/小写/剔除合成主力/截断 100"""
    out = cap_symbol_list(
        ["RB2610", "rb2610", " au2612 ", "", "rb9999", "rb9998", "ma8888", "cu2607"]
    )
    assert out == ["rb2610", "au2612", "cu2607"]
    # 上限截断
    many = [f"ab{i:03d}" for i in range(150)]
    assert len(cap_symbol_list(many)) == 100
    # 空/None 输入
    assert cap_symbol_list([]) == []
    assert cap_symbol_list(["", None]) == []  # type: ignore[list-item]
