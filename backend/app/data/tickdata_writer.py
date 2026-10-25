"""pg-tickdata K线写入器 —— VVTR 拉取的 K线落库（upsert 幂等）

表 kline（月分区 264 个，PK (period, instrument, ts)）：
- period smallint(1/5/15/30/60/1440)、exchange 大写代码(SHFE..)、instrument 小写
- ts 为 naive 北京时间且是 **bar 结束时间（eob）**——平台 bar.time 为
  bar 开始时间（bob，VVTR b 字段），写入时分钟线 +1 个周期；
  日线 ts 取日期 00:00:00。
- amount/position 可空。

写入策略：仅写 VVTR 源数据（质量最高，避免低质源覆盖）；
ON CONFLICT 更新（forming bar 收敛后重写同 ts 即修正）；
(symbol, period) 60 秒节流防高频重写。
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Any

from app.data.tickdata_kline import PERIOD_MAP, _get_pool

logger = logging.getLogger("qihuo.tickdata.writer")

# 同 (symbol, period) 写入节流窗口
_PERSIST_THROTTLE_SEC = 60.0
_last_persist: dict[tuple[str, str], float] = {}

# 中文交易所名（contracts.py）→ tickdata exchange 大写代码
_CN_EXCHANGE_TO_CODE: dict[str, str] = {
    "上期所": "SHFE",
    "上海期货交易所": "SHFE",
    "能源中心": "INE",
    "上海国际能源": "INE",
    "大商所": "DCE",
    "大连商品交易所": "DCE",
    "郑商所": "CZCE",
    "郑州商品交易所": "CZCE",
    "中金所": "CFFEX",
    "中国金融期货交易所": "CFFEX",
    "广期所": "GFEX",
    "广州期货交易所": "GFEX",
}


def resolve_exchange_code(symbol: str) -> str:
    """合约 → tickdata exchange 大写代码（查静态合约表；未知返回 SHFE 兜底）"""
    try:
        from app.data.contracts import ALL_CONTRACTS

        target = str(symbol or "").strip().lower()
        for contract in ALL_CONTRACTS:
            if str(contract.get("symbol") or "").lower() == target:
                code = _CN_EXCHANGE_TO_CODE.get(str(contract.get("exchange") or ""))
                if code:
                    return code
    except Exception:
        pass
    return "SHFE"


def bar_to_row(
    symbol: str,
    period: str,
    bar: dict[str, Any],
    exchange_code: str | None = None,
) -> tuple | None:
    """标准 bar（time=bob）→ kline 行元组（ts=eob；纯函数，供单测）"""
    pg_period = PERIOD_MAP.get(period)
    time_str = str(bar.get("time") or "").strip()
    if pg_period is None or not time_str:
        return None
    if period == "1d":
        try:
            ts = datetime.strptime(time_str[:10], "%Y-%m-%d")
        except ValueError:
            return None
    else:
        try:
            ts = datetime.strptime(time_str[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
        ts = ts + timedelta(minutes=pg_period)  # bob → eob
    try:
        volume = int(float(bar.get("volume") or 0))
        position = bar.get("open_interest")
        position_val = (
            float(position) if position not in (None, "") else None
        )
    except (TypeError, ValueError):
        return None
    instrument = str(symbol or "").strip().lower()
    if not instrument:
        return None
    return (
        pg_period,
        exchange_code or resolve_exchange_code(instrument),
        instrument,
        ts,
        float(bar.get("open") or 0),
        float(bar.get("high") or 0),
        float(bar.get("low") or 0),
        float(bar.get("close") or 0),
        volume,
        None,  # amount（VVTR bar 无成交额）
        position_val,
    )


_UPSERT_SQL = (
    "INSERT INTO kline (period, exchange, instrument, ts, "
    "open, high, low, close, volume, amount, position) "
    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) "
    "ON CONFLICT (period, instrument, ts) DO UPDATE SET "
    "exchange=EXCLUDED.exchange, open=EXCLUDED.open, high=EXCLUDED.high, "
    "low=EXCLUDED.low, close=EXCLUDED.close, volume=EXCLUDED.volume, "
    "amount=EXCLUDED.amount, position=EXCLUDED.position"
)


async def persist_bars(
    symbol: str,
    period: str,
    bars: list[dict[str, Any]],
    *,
    force: bool = False,
) -> int:
    """VVTR bar 列表 upsert 进 pg-tickdata（幂等；失败仅记日志不抛）"""
    if not bars:
        return 0
    pool = await _get_pool()
    if pool is None:
        return 0
    key = (str(symbol or "").lower(), period)
    now = time.monotonic()
    if not force and now - _last_persist.get(key, 0.0) < _PERSIST_THROTTLE_SEC:
        return 0
    exchange_code = resolve_exchange_code(key[0])
    rows = []
    for bar in bars:
        row = bar_to_row(key[0], period, bar, exchange_code)
        if row is not None:
            rows.append(row)
    if not rows:
        return 0
    try:
        async with pool.acquire() as conn:
            await conn.executemany(_UPSERT_SQL, rows)
        _last_persist[key] = now
        return len(rows)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning(
            "TickData 写入失败 %s %s（%d 行）",
            key[0], period, len(rows), exc_info=True,
        )
        return 0


def persist_bars_task(
    symbol: str,
    period: str,
    bars: list[dict[str, Any]],
) -> asyncio.Task | None:
    """fire-and-forget 写入（供 kline_provider 成功路径调用）"""
    try:
        return asyncio.create_task(persist_bars(symbol, period, bars))
    except RuntimeError:
        return None
