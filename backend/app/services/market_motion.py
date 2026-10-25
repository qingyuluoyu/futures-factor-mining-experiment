"""盘口动能检测 —— 值不动的行情视为休市（节假日/异常闭市兜底）

时段表只认「工作日 + 节段」，无节假日日历：法定节假日在日历上是工作日，
会被误判为开盘。本模块按盘口指纹（最新价/买卖一档/成交量/持仓）是否
变化来判定真实开闭市：

- 节假日期间数据源可能仍推送「冻结快照」（recv_ts 是新的、值不动），
  因此必须比较值本身而非接收时间；
- 指纹变化 → 记录时刻；超过阈值无变化（或行情缺失）→ 视为休市；
- 首次观测给一个完整宽限期（从记录时刻起算），避免冷启动误杀。

供 AI 任务休市同步（session_auto）消费；Redis 不可用时恒返回 False
（回落到纯时段表行为）。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from app.services.market_keys import mode_ns, quote_key

logger = logging.getLogger("qihuo.market_motion")

# 视为休市的静默阈值（秒）：流动品种开盘中价/量/档位 10 分钟完全无变化
# 实际不可能发生；取宽裕值避免数据抖动误杀
DEFAULT_STALE_SECONDS = 600.0

# 参与指纹的字段：任一变化即视为「盘口动了」
_FP_FIELDS = (
    "last_price",
    "volume",
    "bid_price1",
    "ask_price1",
    "bid_volume1",
    "ask_volume1",
    "open_interest",
)


def _motion_key(trading_mode: str | None, symbol: str) -> str:
    return f"market:motion:{mode_ns(trading_mode)}:{symbol.strip().lower()}"


def _fingerprint(quote: dict[str, Any]) -> str:
    """盘口指纹：关键字段拼接（None/缺失也参与，缺失→有值即为变化）"""
    return "|".join(f"{f}={quote.get(f)}" for f in _FP_FIELDS)


def _quote_fingerprint(raw: Any) -> str | None:
    """Redis 里合并行情 JSON → 指纹；无数据/解析失败返回 None"""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return _fingerprint(data)


async def is_symbol_motion_stale(
    redis_conn: Any,
    symbol: str,
    *,
    trading_mode: str | None = "live",
    stale_s: float = DEFAULT_STALE_SECONDS,
    now_ts: float | None = None,
) -> bool:
    """时段表开盘的前提下，盘口超过 stale_s 无变化 → True（视为休市）"""
    if redis_conn is None or not symbol:
        return False
    now = float(now_ts if now_ts is not None else time.time())
    sym = symbol.strip().lower()

    try:
        fp_now = _quote_fingerprint(
            await redis_conn.get(quote_key(trading_mode, sym))
        )
        motion = await redis_conn.hgetall(_motion_key(trading_mode, sym))
        if not motion or "fp" not in motion:
            # 首次观测：记录指纹，从现在起算宽限期
            await redis_conn.hset(
                _motion_key(trading_mode, sym),
                mapping={"fp": fp_now if fp_now is not None else "", "ts": now},
            )
            return False
        last_fp = str(motion.get("fp") or "")
        try:
            last_ts = float(motion.get("ts") or 0)
        except (TypeError, ValueError):
            last_ts = 0.0
        if fp_now is not None and fp_now != last_fp:
            await redis_conn.hset(
                _motion_key(trading_mode, sym),
                mapping={"fp": fp_now, "ts": now},
            )
            return False
        # 值未变化（或行情缺失）：超过阈值视为休市
        return (now - last_ts) >= float(stale_s)
    except Exception:
        # Redis 异常：回落到时段表行为，不因辅助检测失败阻断主流程
        logger.warning("盘口动能检测异常 symbol=%s", sym, exc_info=True)
        return False
