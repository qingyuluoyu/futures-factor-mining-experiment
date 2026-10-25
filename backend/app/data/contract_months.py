"""全品种交割月份合约清单 —— 按品种代码生成全部月份合约 symbol

不同交易所品种的交割月份规则不同:
- 上期所(SHFE)、大商所(DCE)、郑商所(CZCE)、能源中心(INE):多数是 1/5/9 或 1/5/10 月
- 广期所(GFEX):工业硅 1/5/9,碳酸锂 1/5/9
- 中金所(CFFEX)股指期货(IF/IC/IH/IM):当月、下月、随后两个季月(动态)
- 中金所国债期货(T/TF/TS/TL):最近 3 个季月(动态)

symbol 格式:小写 code + YYMM,如 rb2610、au2612、if2606。
"""

from datetime import datetime, timedelta
from typing import Any

# 各品种代码 → 常见交割月份(1-12)
# 中金所股指和国债不在此处,用动态生成函数处理
CODE_MONTHS_MAP: dict[str, list[int]] = {
    # ===== 上期所 (SHFE) =====
    "RB": [1, 5, 10], "HC": [1, 5, 10], "CU": [1, 5, 9], "AL": [1, 5, 9],
    "ZN": [1, 5, 9], "PB": [1, 5, 9], "NI": [1, 5, 9], "SN": [1, 5, 9],
    "AU": [2, 4, 6, 8, 10, 12], "AG": [2, 4, 6, 8, 10, 12],
    "SS": [1, 5, 9], "BU": [1, 5, 9], "RU": [1, 3, 5, 7, 9, 11],
    "NR": [1, 5, 9], "SP": [1, 5, 9], "WR": [1, 5, 9],

    # ===== 大商所 (DCE) =====
    "I": [1, 5, 9], "J": [1, 5, 9], "JM": [1, 5, 9], "M": [1, 5, 9],
    "Y": [1, 5, 9], "P": [1, 5, 9], "C": [1, 3, 5, 7, 9, 11],
    "CS": [1, 3, 5, 7, 9, 11], "A": [1, 3, 5, 7, 9, 11], "B": [1, 3, 5, 7, 9, 11],
    "JD": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], "RR": [1, 5, 9],
    "L": [1, 5, 9], "V": [1, 5, 9], "PP": [1, 5, 9], "EG": [1, 5, 9],
    "EB": [1, 5, 9], "PG": [1, 5, 9], "FB": [1, 5, 9], "BB": [1, 5, 9],

    # ===== 郑商所 (CZCE) =====
    "CF": [1, 3, 5, 7, 9, 11], "CY": [1, 3, 5, 7, 9, 11], "SR": [1, 3, 5, 7, 9, 11],
    "TA": [1, 3, 5, 7, 9, 11], "MA": [1, 3, 5, 7, 9, 11], "FG": [1, 3, 5, 7, 9, 11],
    "SA": [1, 3, 5, 7, 9, 11], "UR": [1, 3, 5, 7, 9, 11], "AP": [1, 3, 5, 7, 9, 10, 11, 12],
    "CJ": [1, 3, 5, 7, 9, 11], "WH": [1, 3, 5, 7, 9, 11], "PM": [1, 3, 5, 7, 9, 11],
    "RI": [1, 3, 5, 7, 9, 11], "RS": [1, 3, 5, 7, 9, 11], "RM": [1, 3, 5, 7, 9, 11],
    "OI": [1, 3, 5, 7, 9, 11], "PF": [1, 3, 5, 7, 9, 11], "SH": [1, 3, 5, 7, 9, 11],
    "LH": [1, 3, 5, 7, 9, 11],

    # ===== 广期所 (GFEX) =====
    "SI": [1, 5, 9], "LC": [1, 5, 9],

    # ===== 能源中心 (INE) =====
    "SC": [1, 3, 5, 7, 9], "LU": [1, 3, 5, 7, 9], "BC": [1, 3, 5, 7, 9],
    "EC": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
}

# 中金所股指期货品种(IF/IC/IH/IM)
_CFFEX_INDEX_CODES: set[str] = {"IF", "IC", "IH", "IM"}

# 中金所国债期货品种(T/TF/TS/TL)
_CFFEX_BOND_CODES: set[str] = {"T", "TF", "TS", "TL"}

# 合约元数据(从 contracts.py 同步,避免循环导入)
from app.data.contracts import ALL_CONTRACTS  # noqa: E402

# 品种代码 → (name, exchange) 映射,用于生成合约元数据
_CODE_META: dict[str, tuple[str, str]] = {}
for _c in ALL_CONTRACTS:
    code = _c["code"]
    if code not in _CODE_META:
        _CODE_META[code] = (_c["name"], _c["exchange"])


def _is_cffex_index(code: str) -> bool:
    """判断是否为中金所股指品种(IF/IC/IH/IM)"""
    return code in _CFFEX_INDEX_CODES


def _is_cffex_bond(code: str) -> bool:
    """判断是否为中金所国债品种(T/TF/TS/TL)"""
    return code in _CFFEX_BOND_CODES


def generate_cffex_index_months(now: datetime) -> list[tuple[int, int]]:
    """生成中金所股指期货的合约月份:当月、下月、随后两个季月(3/6/9/12)

    返回 [(year, month), ...] 升序排列,共 4 个合约月份。
    """
    year = now.year
    month = now.month

    # 季月:3/6/9/12
    quarter_months = [3, 6, 9, 12]

    # 当月
    months: list[tuple[int, int]] = [(year, month)]

    # 下月
    nm_year, nm_month = (year, month + 1) if month < 12 else (year + 1, 1)
    months.append((nm_year, nm_month))

    # 随后两个季月(在"下月"之后)
    next_quarters: list[tuple[int, int]] = []
    cur_y, cur_m = nm_year, nm_month
    # 找下月之后的第一个季月
    for i in range(12):
        ny, nm = (cur_y, cur_m + 1) if cur_m < 12 else (cur_y + 1, 1)
        cur_y, cur_m = ny, nm
        if cur_m in quarter_months:
            next_quarters.append((cur_y, cur_m))
            if len(next_quarters) == 2:
                break

    months.extend(next_quarters)
    return months


def generate_bond_months(now: datetime) -> list[tuple[int, int]]:
    """生成中金所国债期货的合约月份:最近 3 个季月(3/6/9/12)

    返回 [(year, month), ...] 升序排列,共 3 个合约月份。
    """
    year = now.year
    month = now.month
    quarter_months = [3, 6, 9, 12]

    # 找当前或之后的第一个季月
    months: list[tuple[int, int]] = []
    cur_y, cur_m = year, month
    for i in range(12):
        if cur_m in quarter_months:
            months.append((cur_y, cur_m))
            if len(months) == 3:
                break
        ny, nm = (cur_y, cur_m + 1) if cur_m < 12 else (cur_y + 1, 1)
        cur_y, cur_m = ny, nm
    return months


def _make_symbol(code: str, year: int, month: int) -> str:
    """生成 symbol:小写 code + 2位年 + 2位月,如 rb2610"""
    return f"{code.lower()}{year % 100:02d}{month:02d}"


def generate_all_month_symbols(now: datetime) -> list[dict[str, Any]]:
    """生成全部品种的全部月份合约清单

    每条记录:{symbol, code, name, exchange, month, year}

    对每个品种生成"当年/次年的所有月份合约",过滤掉已经过去的月份合约。
    """
    result: list[dict[str, Any]] = []

    for code, name_exchange in _CODE_META.items():
        name, exchange = name_exchange

        if _is_cffex_index(code):
            # 中金所股指:动态生成 4 个月份
            months_list = generate_cffex_index_months(now)
        elif _is_cffex_bond(code):
            # 中金所国债:动态生成 3 个季月
            months_list = generate_bond_months(now)
        else:
            # 商品期货:用 CODE_MONTHS_MAP,生成年内+次年共两年
            month_arr = CODE_MONTHS_MAP.get(code, [])
            months_list = []
            for y in [now.year, now.year + 1]:
                for m in month_arr:
                    months_list.append((y, m))

        # 过滤已过期合约(年月 < 当前年月)
        current_ym = now.year * 100 + now.month
        for year, month in months_list:
            if year * 100 + month < current_ym:
                continue
            symbol = _make_symbol(code, year, month)
            result.append({
                "symbol": symbol,
                "code": code,
                "name": name,
                "exchange": exchange,
                "month": month,
                "year": year,
            })

    return result


def get_code_meta(code: str) -> tuple[str, str] | None:
    """获取品种代码的 (name, exchange) 元数据"""
    return _CODE_META.get(code)
