"""品种代码映射测试 —— get_code_for_symbol 的三种形式 + 边界"""

from __future__ import annotations

from app.data.contracts import get_code_for_symbol


def test_specific_month_contract():
    """具体月份合约：rb2610 → RB（原有行为，不能破坏）"""
    assert get_code_for_symbol("rb2610") == "RB"
    assert get_code_for_symbol("cu2607") == "CU"
    assert get_code_for_symbol("au2412") == "AU"


def test_continuous_contract_symbol():
    """连续主力合约：rb888 → RB（本次修复的核心）"""
    assert get_code_for_symbol("rb888") == "RB"
    assert get_code_for_symbol("au888") == "AU"
    assert get_code_for_symbol("cu888") == "CU"
    assert get_code_for_symbol("if888") == "IF"


def test_pure_letter_prefix():
    """纯品种前缀：rb → RB"""
    assert get_code_for_symbol("rb") == "RB"
    assert get_code_for_symbol("au") == "AU"


def test_case_insensitive():
    """大小写不敏感：RB888 / Rb888 都应命中"""
    assert get_code_for_symbol("RB888") == "RB"
    assert get_code_for_symbol("Rb888") == "RB"
    assert get_code_for_symbol("RB2610") == "RB"


def test_unknown_symbol_returns_empty():
    """未知符号返回空字符串（不抛异常）"""
    assert get_code_for_symbol("zzz999") == ""
    assert get_code_for_symbol("") == ""
    assert get_code_for_symbol("12345") == ""  # 纯数字无字母前缀


def test_get_master_symbol_for_code():
    """任意品种形式 → 主力合约符号（与因子实验室统一口径）"""
    from app.data.contracts import get_master_symbol_for_code

    # 品种 code → 主力合约
    assert get_master_symbol_for_code("rb") == "rb2701"
    assert get_master_symbol_for_code("RB") == "rb2701"
    assert get_master_symbol_for_code("au") == "au2612"
    # 连续主力 → 主力合约
    assert get_master_symbol_for_code("rb888") == "rb2701"
    # 已是具体合约 → 原样（同品种的主力）
    assert get_master_symbol_for_code("rb2610") == "rb2701"
    # 未知 → 原样返回不抛异常
    assert get_master_symbol_for_code("zzz999") == "zzz999"
