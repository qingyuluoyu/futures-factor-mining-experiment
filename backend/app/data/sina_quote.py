"""新浪期货行情数据源 —— 直接调用 hq.sinajs.cn 获取实时数据

支持两种数据格式：
- 商品期货（上期所/大商所/郑商所/广期所/能源中心）: 字段含 name, bid/ask
- 中金所金融期货: 字段无 name，open 在首位

批量拉取：按 30 个合约一组分批请求，避免 URL 过长。
"""

import logging
import re
import time
from typing import Any

import httpx

from app.data.contracts import ALL_CONTRACTS

logger = logging.getLogger(__name__)

# 新浪行情 API
_SINA_QUOTE_URL = "https://hq.sinajs.cn/list="
_SINA_HEADERS = {"Referer": "https://finance.sina.com.cn"}

# 批量拉取每批最大数量
_BATCH_SIZE = 30

# 请求超时（秒）
_REQUEST_TIMEOUT = 10

# 中金所品种代码集合（用于区分数据格式）
_CFFEX_CODES: set[str] = {"IF", "IC", "IM", "IH", "TF", "T", "TS", "TL"}


def _build_sina_symbol(contract: dict[str, str]) -> str:
    """将合约信息转换为新浪行情请求代码

    格式: nf_ + symbol(大写)  (如 nf_RB2610, nf_IF2606)
    """
    return f"nf_{contract['symbol'].upper()}"


def _parse_commodity_quote(
    fields: list[str],
    contract: dict[str, str],
) -> dict[str, Any] | None:
    """解析商品期货行情（上期所/大商所/郑商所/广期所/能源中心）

    字段映射（hq.sinajs.cn nf_ 商品，与东方财富等官方盘口对照）：
      [0] name  [1] time(HHmmss)
      [2] open（交易日开盘，含夜盘首开）
      [3] high  [4] low  [5] 买价/备用
      [6] bid  [7] ask  [8] last
      [9] avg/今结  [10] pre_settle 昨结
      [11] bid_vol  [12] ask_vol
      [13] open_interest 持仓  [14] volume 成交量
      [17] date

    注意：持仓与成交量顺序曾与常见现货接口相反，必须以官方
    「成交量/持仓」数值交叉验证（如 PP 成交≈78万、持仓≈47万）。
    """
    try:
        last_price = float(fields[8])
        pre_settle = float(fields[10])
        change = round(last_price - pre_settle, 10)
        change_pct = round((change / pre_settle) * 100, 4) if pre_settle else 0.0
        bid_vol = int(float(fields[11])) if len(fields) > 11 and fields[11] else 0
        ask_vol = int(float(fields[12])) if len(fields) > 12 and fields[12] else 0
        # 商品期货：f13=持仓，f14=成交量（勿对调）
        open_interest = int(float(fields[13])) if len(fields) > 13 and fields[13] else 0
        volume = int(float(fields[14])) if len(fields) > 14 and fields[14] else 0
        avg_price = float(fields[9]) if len(fields) > 9 and fields[9] else 0.0

        return {
            "symbol": contract["symbol"],
            "name": contract["name"],
            "exchange": contract["exchange"],
            "last_price": last_price,
            "change": round(change, 10),
            "change_pct": change_pct,
            "open_price": float(fields[2]),
            "high_price": float(fields[3]),
            "low_price": float(fields[4]),
            # pre_close 字段名沿用；期货语义为昨结 pre_settle
            "pre_close": pre_settle,
            "bid_price": float(fields[6]),
            "ask_price": float(fields[7]),
            "bid_vol": max(0, bid_vol),
            "ask_vol": max(0, ask_vol),
            "volume": max(0, volume),
            "position": max(0, open_interest),
            "avg_price": avg_price,
            "tick_time": fields[1] if len(fields) > 1 else "",
            "trade_date": fields[17] if len(fields) > 17 else "",
        }
    except (ValueError, IndexError):
        logger.debug("解析商品期货行情失败: %s", contract["symbol"])
        return None


def _parse_cffex_quote(
    fields: list[str],
    contract: dict[str, str],
) -> dict[str, Any] | None:
    """解析中金所金融期货行情（IF/IC/IH/IM/TF/T/TS/TL）

    字段映射:
      [0] open  [1] high  [2] low  [3] last_price
      [4] volume  [5] turnover  [6] position
      [13] pre_settle  [14] pre_close
    """
    try:
        last_price = float(fields[3])
        pre_settle = float(fields[13])
        change = round(last_price - pre_settle, 10)
        change_pct = round((change / pre_settle) * 100, 4) if pre_settle else 0.0

        return {
            "symbol": contract["symbol"],
            "name": contract["name"],
            "exchange": contract["exchange"],
            "last_price": last_price,
            "change": round(change, 10),
            "change_pct": change_pct,
            "open_price": float(fields[0]),
            "high_price": float(fields[1]),
            "low_price": float(fields[2]),
            "pre_close": pre_settle,
            "bid_price": last_price,
            "ask_price": last_price,
            "bid_vol": 0,
            "ask_vol": 0,
            "volume": int(float(fields[4])),
            "position": int(float(fields[6])),
            "tick_time": "",
        }
    except (ValueError, IndexError):
        logger.debug("解析中金所期货行情失败: %s", contract["symbol"])
        return None


def _is_cffex(contract: dict[str, str]) -> bool:
    """判断是否为中金所品种"""
    return contract["code"] in _CFFEX_CODES


def _parse_quote_line(
    line: str,
    contract_map: dict[str, dict[str, str]],
) -> dict[str, Any] | None:
    """解析单行新浪行情数据"""
    match = re.match(r'var hq_str_nf_(\w+)="(.+)";', line)
    if not match:
        return None

    symbol = match.group(1).lower()
    content = match.group(2)
    if not content:
        return None

    contract = contract_map.get(symbol)
    if contract is None:
        return None

    fields = content.split(",")

    if _is_cffex(contract):
        return _parse_cffex_quote(fields, contract)
    return _parse_commodity_quote(fields, contract)


def fetch_sina_quote(symbol: str) -> dict[str, Any] | None:
    """拉取单个合约实时行情，失败或未找到返回 None

    优先匹配 ALL_CONTRACTS 中的元数据；未收录的合约仍按 symbol 请求，
    并从品种前缀推断 code（用于中金所/商品格式分流）。
    """
    symbol_l = symbol.lower().strip()
    if not symbol_l:
        return None

    contract = next(
        (c for c in ALL_CONTRACTS if c["symbol"].lower() == symbol_l),
        None,
    )
    if contract is None:
        # 从 symbol 前缀提取品种代码（如 rb2610 → RB）
        code_chars = "".join(ch for ch in symbol_l if ch.isalpha()).upper()
        contract = {
            "symbol": symbol_l,
            "code": code_chars,
            "name": symbol_l,
            "exchange": "",
        }

    contract_map: dict[str, dict[str, str]] = {contract["symbol"].lower(): contract}
    quotes = _fetch_batch([contract], contract_map)
    return quotes[0] if quotes else None


def fetch_sina_quotes() -> list[dict[str, Any]]:
    """从新浪 API 批量拉取所有合约实时行情

    返回成功解析的行情列表，失败的合约自动跳过。
    """
    # 构建 symbol -> contract 映射
    contract_map: dict[str, dict[str, str]] = {
        c["symbol"].lower(): c for c in ALL_CONTRACTS
    }

    # 分批请求
    all_quotes: list[dict[str, Any]] = []
    contracts_batch: list[dict[str, str]] = []

    for contract in ALL_CONTRACTS:
        contracts_batch.append(contract)
        if len(contracts_batch) >= _BATCH_SIZE:
            quotes = _fetch_batch(contracts_batch, contract_map)
            all_quotes.extend(quotes)
            contracts_batch = []
            time.sleep(0.1)

    # 处理最后一批
    if contracts_batch:
        quotes = _fetch_batch(contracts_batch, contract_map)
        all_quotes.extend(quotes)

    logger.info("新浪行情拉取完成，成功 %d / %d 个合约", len(all_quotes), len(ALL_CONTRACTS))
    return all_quotes


def _fetch_batch(
    contracts: list[dict[str, str]],
    contract_map: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    """拉取一批合约的行情数据"""
    sina_symbols = [_build_sina_symbol(c) for c in contracts]
    url = f"{_SINA_QUOTE_URL}{','.join(sina_symbols)}"

    try:
        resp = httpx.get(url, headers=_SINA_HEADERS, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
    except httpx.HTTPError:
        logger.warning("新浪行情请求失败（%d 个合约）", len(contracts))
        return []

    quotes: list[dict[str, Any]] = []
    for line in resp.text.strip().split("\n"):
        quote = _parse_quote_line(line, contract_map)
        if quote is not None:
            quotes.append(quote)

    return quotes
