"""CTP 合约代码映射 —— 仅同月别名（大小写、三/四位年）

禁止把不同月份映射到主合约：否则 openctp 多月 tick 会串价，
虚拟盘 K 线出现 3073↔3135 这种分钟级乱跳。

订阅仍可扩展近月（用于发现 openctp 挂牌）；回写只认「同月」别名。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Iterable

from app.data.contracts import ALL_CONTRACTS

_SYM_RE = re.compile(r"^([A-Za-z]+)(\d{3,4})$")
_BJ = timezone(timedelta(hours=8))


def _unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        s = str(x or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def is_czce_exchange(exchange: str | None) -> bool:
    e = str(exchange or "")
    return "郑" in e or e.upper() == "CZCE"


def _rolling_yymm(count: int = 14) -> list[str]:
    now = datetime.now(_BJ)
    out: list[str] = []
    y, m = now.year, now.month
    for _ in range(count):
        out.append(f"{y % 100:02d}{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def same_month_aliases(symbol: str) -> list[str]:
    """同月别名：大小写 + 三/四位年互换（CF2609 ↔ CF609）"""
    base = str(symbol or "").strip()
    if not base:
        return []
    variants: list[str] = [base, base.lower(), base.upper()]
    m = _SYM_RE.match(base)
    if not m:
        return _unique(variants)
    code, digits = m.group(1), m.group(2)
    if len(digits) == 4:
        # 2609 → 609（取年后一位 + 月）
        short = code + digits[1:]
        variants.extend([short, short.lower(), short.upper()])
    elif len(digits) == 3:
        # 609 → 2609（2020 年代默认前缀 2）
        long4 = code + "2" + digits
        variants.extend([long4, long4.lower(), long4.upper()])
    return _unique(variants)


def expand_symbol_aliases(symbol: str, exchange: str | None = None) -> list[str]:
    """兼容旧名：仅同月别名（exchange 参数保留，不跨月）"""
    _ = exchange
    return same_month_aliases(symbol)


def expand_product_month_instruments(product_code: str) -> list[str]:
    """某品种近月 Instrument（仅用于订阅发现，不用于跨月回写）"""
    code = str(product_code or "").strip()
    if not code:
        return []
    ids: list[str] = []
    for yymm in _rolling_yymm(14):
        ymm = yymm[1:]
        for form in (yymm, ymm):
            ids.append(code.lower() + form)
            ids.append(code.upper() + form)
    return _unique(ids)


def build_subscribe_instruments(
    contracts: list[dict[str, str]] | None = None,
) -> list[str]:
    """订阅：静态合约同月别名 + 全品种近月（扩大命中 openctp 挂牌）"""
    src = contracts if contracts is not None else ALL_CONTRACTS
    ids: list[str] = []
    product_codes: set[str] = set()
    for c in src:
        sym = str(c.get("symbol") or "")
        ids.extend(same_month_aliases(sym))
        m = _SYM_RE.match(sym)
        if m:
            product_codes.add(m.group(1))
        code = str(c.get("code") or "").strip()
        if code:
            product_codes.add(code)
    for code in product_codes:
        ids.extend(expand_product_month_instruments(code))
    return _unique(ids)


def build_instrument_to_canonical(
    contracts: list[dict[str, str]] | None = None,
) -> dict[str, str]:
    """别名 → canonical，**仅同月**

    cu2609 不会映射到 cu2607；只有 cu607 / CU2607 → cu2607。
    """
    src = contracts if contracts is not None else ALL_CONTRACTS
    mapping: dict[str, str] = {}
    for c in src:
        canon = str(c.get("symbol") or "").strip().lower()
        if not canon:
            continue
        for alt in same_month_aliases(canon):
            mapping[alt.lower()] = canon
        mapping[canon] = canon
    return mapping


def resolve_canonical_symbol(
    instrument_id: str,
    alias_map: dict[str, str] | None = None,
) -> str:
    """CTP InstrumentID → 内部 symbol；不同月不串，未知则原样小写"""
    raw = str(instrument_id or "").strip()
    if not raw:
        return ""
    m = alias_map if alias_map is not None else build_instrument_to_canonical()
    key = raw.lower()
    if key in m:
        return m[key]
    # 仅同月：三位数年扩成 2+ymm 再查
    mm = _SYM_RE.match(raw)
    if mm and len(mm.group(2)) == 3:
        guess = (mm.group(1) + "2" + mm.group(2)).lower()
        if guess in m:
            return m[guess]
    if mm and len(mm.group(2)) == 4:
        guess = (mm.group(1) + mm.group(2)[1:]).lower()
        if guess in m:
            return m[guess]
    # 不在合约表：返回自身小写（调用方应丢弃非表内合约的写入）
    return key


def is_known_contract_symbol(
    symbol: str,
    alias_map: dict[str, str] | None = None,
) -> bool:
    """是否为 ALL_CONTRACTS 内可识别合约（同月别名）"""
    m = alias_map if alias_map is not None else build_instrument_to_canonical()
    key = str(symbol or "").strip().lower()
    if not key:
        return False
    if key in m:
        return True
    canon = resolve_canonical_symbol(key, m)
    return canon in m and m.get(canon) == canon or canon in set(m.values())
