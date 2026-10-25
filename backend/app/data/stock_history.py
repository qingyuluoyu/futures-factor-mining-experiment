"""A 股历史数据获取层 —— Tushare 协议（DaoShare 代理）

四块数据：
- 股票池：指数成分权重 index_weight（沪深300 / 中证800）
- 标的静态信息：stock_basic（代码/名称/行业/上市日期，一次拉全市场）
- 日线：daily（未复权）+ adj_factor → 前复权 OHLCV
- 每日估值：daily_basic（PE(TTM)/PB/总市值/流通市值/换手率）
- 季度财务：fina_indicator（ROE/毛利率/营收同比/净利润同比/EPS/BPS + 公告日期）

财务的 ann_date（公告日期）用于前视偏差防护：因子只允许使用
公告日 <= 决策日 的财报数据。

批量约定：代理单次响应约 6000 行上限，日线/估值按 3 只一批、
财务按 10 只一批（每只约 60 个报告期）。
"""

from __future__ import annotations

from datetime import date
import math
from typing import Any

from app.data.tushare_client import get_client

_INDEX_CODES = {"hs300": "000300.SH", "csi800": "000906.SH"}

# stock_basic 一次性拉全市场，进程内缓存
_STOCK_BASIC_CACHE: dict[str, dict[str, Any]] | None = None


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (ValueError, TypeError):
        return None


def _to_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    if len(text) != 8 and len(text) == 10:
        text = text.replace("-", "")
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    except ValueError:
        return None


def _six(code: str) -> str:
    """Tushare ts_code（000001.SZ）→ 6位代码（000001）"""
    return str(code).split(".")[0].zfill(6)


def fetch_universe(index: str = "hs300") -> list[dict[str, str]]:
    """拉取股票池（默认沪深300；csi800 为中证800）

    取指数权重的最新公布日成分。Returns: [{code, name}] 按代码升序。
    """
    client = get_client()
    rows = client.query("index_weight", {"index_code": _INDEX_CODES.get(index, "000300.SH")})
    if not rows:
        return []
    latest = max(str(r.get("trade_date") or "") for r in rows)
    codes = sorted({_six(r["con_code"]) for r in rows if str(r.get("trade_date")) == latest})
    names = _stock_basic_map()
    return [{"code": c, "name": names.get(c, {}).get("name", c)} for c in codes]


def _stock_basic_map() -> dict[str, dict[str, Any]]:
    """全市场股票静态信息（6位code → name/industry/list_date），进程内缓存"""
    global _STOCK_BASIC_CACHE
    if _STOCK_BASIC_CACHE is None:
        client = get_client()
        rows = client.query(
            "stock_basic",
            {"list_status": "L"},
            "ts_code,name,industry,market,list_date",
        )
        _STOCK_BASIC_CACHE = {
            _six(r["ts_code"]): {
                "name": r.get("name") or "",
                "industry": r.get("industry") or "",
                "list_date": _to_date(r.get("list_date")),
            }
            for r in rows
        }
    return _STOCK_BASIC_CACHE


def fetch_stock_basic(codes: list[str]) -> list[dict[str, Any]]:
    """批量取标的静态信息（含行业/上市日期）"""
    names = _stock_basic_map()
    return [
        {
            "code": c,
            "name": names.get(c, {}).get("name", c),
            "industry": names.get(c, {}).get("industry", ""),
            "list_date": names.get(c, {}).get("list_date"),
        }
        for c in codes
    ]


def _with_suffix(code: str) -> str:
    """6 位代码 → Tushare ts_code（带交易所后缀）"""
    if "." in code:
        return code
    if code.startswith(("8", "4", "920")):
        return f"{code}.BJ"
    if code.startswith(("60", "68", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"


DAILY_BATCH_SIZE = 2
FINA_BATCH_SIZE = 10


def _chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def fetch_daily_batch(
    codes: list[str],
    start_date: str,
    end_date: str = "",
) -> dict[str, list[dict[str, Any]]]:
    """批量拉取前复权日线（2 只/批，分页和逐日复权因子必须完整）

    daily（未复权）+ adj_factor 合并：
        前复权价 = 原始价 × adj_factor(t) / adj_factor(该代码最后交易日)

    Returns: {code: [{trade_date, open, high, low, close, volume}]} 升序
    """
    client = get_client()
    if not end_date:
        end_date = date.today().strftime("%Y%m%d")
    start, end = _to_date(start_date), _to_date(end_date)
    if start is None or end is None or start > end:
        raise ValueError("历史导入日期区间无效")
    result: dict[str, list[dict[str, Any]]] = {}
    for batch in _chunked(codes, DAILY_BATCH_SIZE):
        ts_codes = ",".join(_with_suffix(c) for c in batch)
        params = {"ts_code": ts_codes, "start_date": start_date, "end_date": end_date}

        daily_page = client.query_page("daily", params)
        if daily_page.has_more is not False:
            raise ValueError("历史日线分页完整性未确认，未继续请求复权因子")
        factor_page = client.query_page("adj_factor", params)
        if factor_page.has_more is not False:
            raise ValueError("历史日线或复权因子分页完整性未确认，未导入截断结果")
        bars, factors = daily_page.records, factor_page.records
        expected = set(ts_codes.split(","))

        factor_by_day: dict[tuple[str, date], float] = {}
        for r in factors:
            identifier = r.get("ts_code")
            factor_date = _to_date(r.get("trade_date"))
            value = _to_float(r.get("adj_factor"))
            if (identifier not in expected or factor_date is None or not start <= factor_date <= end
                    or isinstance(r.get("adj_factor"), bool) or value is None or not math.isfinite(value) or value <= 0):
                raise ValueError("历史复权因子标识、日期或数值无效，未使用默认因子")
            factor_key = (identifier, factor_date)
            if factor_key in factor_by_day:
                raise ValueError("历史复权因子重复")
            factor_by_day[factor_key] = value

        normalized_bars, seen, last_dates = [], set(), {}
        for record in bars:
            identifier = record.get("ts_code")
            trade_date = _to_date(record.get("trade_date"))
            prices = {name: _to_float(record.get(name)) for name in ("open", "high", "low", "close")}
            volume = _to_float(record.get("vol"))
            if (identifier not in expected or trade_date is None or not start <= trade_date <= end
                    or any(isinstance(record.get(name), bool) or value is None or not math.isfinite(value) or value <= 0 for name, value in prices.items())
                    or isinstance(record.get("vol"), bool) or volume is None or not math.isfinite(volume) or volume < 0
                    or not prices["low"] <= min(prices["open"], prices["close"]) <= max(prices["open"], prices["close"]) <= prices["high"]):
                raise ValueError("历史日线标识、日期、价量或OHLC无效，未跳过或填零")
            key = (identifier, trade_date)
            if key in seen:
                raise ValueError("历史日线重复")
            seen.add(key)
            last_dates[identifier] = max(last_dates.get(identifier, trade_date), trade_date)
            normalized_bars.append({"identifier": identifier, "trade_date": trade_date, **prices, "volume": volume})

        grouped: dict[str, list[dict[str, Any]]] = {}
        for record in normalized_bars:
            identifier, trade_date = record["identifier"], record["trade_date"]
            base = factor_by_day.get((identifier, last_dates[identifier]))
            factor = factor_by_day.get((identifier, trade_date))
            if base is None or factor is None:
                raise ValueError("历史复权因子缺失，未按1或其他日期因子补值")
            code = identifier.split(".")[0]
            ratio = factor / base
            adjusted = {field: round(record[field] * ratio, 4) for field in ("open", "high", "low", "close")}
            if any(not math.isfinite(value) or value <= 0 for value in adjusted.values()):
                raise ValueError("复权价格超出有限正数精度边界，未存储零价或无穷大")
            grouped.setdefault(code, []).append(
                {
                    "trade_date": trade_date,
                    **adjusted,
                    "volume": record["volume"],
                }
            )
        for code, records in grouped.items():
            records.sort(key=lambda x: x["trade_date"])
            result[code] = records
    return result


def fetch_daily_basic_batch(
    codes: list[str],
    start_date: str,
    end_date: str = "",
) -> dict[str, list[dict[str, Any]]]:
    """批量拉取每日估值（PE/PB/市值/换手率，3 只/批）

    市值单位由 Tushare 的万元换算为亿元。
    """
    client = get_client()
    if not end_date:
        end_date = date.today().strftime("%Y%m%d")
    result: dict[str, list[dict[str, Any]]] = {}
    for batch in _chunked(codes, DAILY_BATCH_SIZE):
        ts_codes = ",".join(_with_suffix(c) for c in batch)
        rows = client.query(
            "daily_basic",
            {"ts_code": ts_codes, "start_date": start_date, "end_date": end_date},
        )
        for r in rows:
            code = str(r["ts_code"]).split(".")[0].zfill(6)
            trade_date = _to_date(r.get("trade_date"))
            if trade_date is None:
                continue
            total_mv = _to_float(r.get("total_mv"))
            circ_mv = _to_float(r.get("circ_mv"))
            result.setdefault(code, []).append(
                {
                    "trade_date": trade_date,
                    "pe_ttm": _to_float(r.get("pe_ttm")),
                    "pb": _to_float(r.get("pb")),
                    "total_mv": round(total_mv / 10000, 4) if total_mv is not None else None,
                    "circ_mv": round(circ_mv / 10000, 4) if circ_mv is not None else None,
                    "turnover_rate": _to_float(r.get("turnover_rate")),
                }
            )
    for records in result.values():
        records.sort(key=lambda x: x["trade_date"])
    return result


def fetch_fina_batch(codes: list[str]) -> dict[str, list[dict[str, Any]]]:
    """批量拉取财务指标（10 只/批，全部报告期）

    ann_date（公告日期）→ publish_date，用于前视偏差防护。
    """
    client = get_client()
    result: dict[str, list[dict[str, Any]]] = {}
    for batch in _chunked(codes, FINA_BATCH_SIZE):
        ts_codes = ",".join(_with_suffix(c) for c in batch)
        rows = client.query(
            "fina_indicator",
            {"ts_code": ts_codes, "start_date": "20180101"},
            "ts_code,ann_date,end_date,roe,grossprofit_margin,or_yoy,"
            "netprofit_yoy,eps,bps,dt_netprofit",
        )
        for r in rows:
            code = str(r["ts_code"]).split(".")[0].zfill(6)
            report_date = _to_date(r.get("end_date"))
            if report_date is None:
                continue
            result.setdefault(code, []).append(
                {
                    "code": code,
                    "report_date": report_date,
                    "publish_date": _to_date(r.get("ann_date")),
                    "roe": _to_float(r.get("roe")),
                    "gross_margin": _to_float(r.get("grossprofit_margin")),
                    "revenue_yoy": _to_float(r.get("or_yoy")),
                    "netprofit_yoy": _to_float(r.get("netprofit_yoy")),
                    "eps": _to_float(r.get("eps")),
                    "bps": _to_float(r.get("bps")),
                    "netprofit": _to_float(r.get("dt_netprofit")),
                }
            )
    for records in result.values():
        records.sort(key=lambda x: x["report_date"])
    return result
