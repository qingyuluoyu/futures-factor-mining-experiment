"""委托价格带校验 —— 相对最新价 ±10%

超出带外的挂单/市价对模拟交易无实际意义，统一拒绝。
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from fastapi import HTTPException

from app.data.contracts import get_mock_base_price
from app.core.trading_mode import MODE_LIVE, normalize_trading_mode

# 允许偏离最新价的最大比例
PRICE_BAND_RATIO: Decimal = Decimal("0.10")


async def resolve_last_price(
    symbol: str,
    redis_conn: Any | None,
    fallback: float | None,
    trading_mode: str = "live",
    allow_mock: bool = True,
) -> Decimal | None:
    """解析最新参考价：Redis 统一行情 > sina 源快照 > 请求传入 > 品种 mock 基准

    sina 源快照（market:live:source:sina:quote:*）由新浪桥常驻双写，
    收盘后仍保留当日最新价——统一报价键缺失时（Redis 重建 + 休市无新
    tick，2026-09-08 rb2701 曾因此落到 mock 默认 5000，浮盈虚增
    +18540），它是真实价兜底。
    allow_mock=False 用于浮盈展示/交易定价/价格带校验：宁可不给参考价
    （调用方自行回落持仓均价或K线收盘价），也不返回与实盘无关的 mock
    基准价。
    """
    symbol_l = symbol.strip().lower()
    if redis_conn is not None:
        try:
            from app.services.market_keys import get_quote_json

            data = await get_quote_json(redis_conn, trading_mode, symbol_l)
            if data:
                last = data.get("last_price")
                if last is not None and float(last) > 0:
                    return Decimal(str(last))
        except Exception:
            pass
        # 统一报价缺失：回落 sina 源快照（真实市场价，含收盘后）
        try:
            from app.services.market_keys import source_quote_key

            raw = await redis_conn.get(source_quote_key("sina", symbol_l)) if normalize_trading_mode(trading_mode) == MODE_LIVE else None
            if raw:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="ignore")
                src = json.loads(raw)
                if isinstance(src, dict):
                    last = src.get("last_price")
                    if last is not None and float(last) > 0:
                        return Decimal(str(last))
        except Exception:
            pass

    if fallback is not None and fallback > 0:
        return Decimal(str(fallback))
    if not allow_mock:
        return None
    mock = get_mock_base_price(symbol_l)
    if mock and mock > 0:
        return Decimal(str(mock))
    return None


def price_band_bounds(last: Decimal) -> tuple[Decimal, Decimal]:
    """返回 (下限, 上限)"""
    lo = (last * (Decimal("1") - PRICE_BAND_RATIO)).quantize(Decimal("0.0001"))
    hi = (last * (Decimal("1") + PRICE_BAND_RATIO)).quantize(Decimal("0.0001"))
    return lo, hi


def assert_price_in_band(
    order_price: Decimal,
    last: Decimal | None,
) -> dict[str, float]:
    """校验委托价在最新价 ±10% 内；失败抛 400

    返回 band 信息供响应附带。
    """
    if last is None or last <= 0:
        # 无任何参考价时放行（极端兜底），避免完全无法交易
        return {
            "last_price": 0.0,
            "band_ratio": float(PRICE_BAND_RATIO),
            "min_price": 0.0,
            "max_price": 0.0,
            "enforced": False,
        }

    lo, hi = price_band_bounds(last)
    if order_price < lo or order_price > hi:
        raise HTTPException(
            status_code=400,
            detail=(
                f"委托价 {float(order_price):.4f} 超出最新价 "
                f"{float(last):.4f} 的 ±{float(PRICE_BAND_RATIO) * 100:.0f}% 范围"
                f"（允许 {float(lo):.4f} ~ {float(hi):.4f}）"
            ),
        )
    return {
        "last_price": float(last),
        "band_ratio": float(PRICE_BAND_RATIO),
        "min_price": float(lo),
        "max_price": float(hi),
        "enforced": True,
    }
