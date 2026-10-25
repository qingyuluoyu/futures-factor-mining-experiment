"""AKShare 历史日线数据获取层 —— 从 AKShare 拉取连续合约日线并标准化"""

from typing import Any


def fetch_daily_history(code: str, start_date: str) -> list[dict[str, Any]]:
    """从 AKShare 拉取日线历史数据（同步调用）

    Args:
        code: 品种代码，如 "AU"
        start_date: 起始日期，格式 "20200101"

    Returns:
        标准化记录列表，按日期升序排列
    """
    import akshare as ak  # noqa: C0415 — 延迟导入，避免启动时加载

    symbol = code.upper() + "0"
    df = ak.futures_main_sina(symbol=symbol, start_date=start_date)
    records: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        normalized = _normalize_row(row)
        if normalized is not None:
            records.append(normalized)
    records.sort(key=lambda r: r["trade_date"])
    return records


def _normalize_row(row: Any) -> dict[str, Any] | None:
    """AKShare DataFrame 行 → 内部格式，无效行返回 None"""
    try:
        return {
            "trade_date": _parse_date(str(row["日期"])),
            "open": float(row["开盘价"]),
            "high": float(row["最高价"]),
            "low": float(row["最低价"]),
            "close": float(row["收盘价"]),
            "volume": int(float(row["成交量"])),
            "hold": int(float(row.get("持仓量", 0))),
        }
    except (ValueError, TypeError, KeyError):
        return None


def _parse_date(date_str: str) -> str:
    """将日期字符串统一为 'YYYY-MM-DD' 格式"""
    cleaned = date_str.strip().replace("/", "-").replace(" ", "")
    parts = cleaned.split("-")
    if len(parts) == 3:
        return f"{parts[0]}-{parts[1].zfill(2)}-{parts[2].zfill(2)}"
    return cleaned
