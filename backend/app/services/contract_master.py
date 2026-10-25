"""主力合约识别服务 —— 基于 Redis 行情数据识别每个品种的主力/次主力合约

主力口径：平台主力表（app.data.contracts，VVTR 迁移基线，商品列表/
行情订阅/K线落库主链路同源）优先，成交量识别仅兜底——量口径在换月
过渡期会滞后（旧主力成交量未衰减，如实测量口径 RB=rb2610 而平台表
=rb2701），全链路必须跟平台表一致。
次主力 = 品种内成交量最大的非主力合约（换月观察对象）。

识别流程:
1. 优先扫描 market:monthly:quote:*（全月合约独立命名空间）
2. 若全月缓存为空，回退扫描 market:quote:*（主行情，兼容降级）
3. 按品种代码分组(从 symbol 中提取 leading letters)
4. 过滤已过期月份合约(year*100+month < 当前年月)
5. 过滤无 volume 的合约(volume=0)
6. 每组按 volume 降序,Top1/Top2 为量口径；master 对齐平台主力表
   （表无该品种时用量口径 Top1），secondary 取量最大的非主力合约
"""

import json
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any

import redis.asyncio as aioredis

from app.data.contracts import get_code_for_symbol, get_master_symbol_for_code

logger = logging.getLogger("qihuo.contract_master")

# 北京时间
_BJ_TZ = timezone(timedelta(hours=8))

# Redis 缓存 key 与 TTL
_MASTER_CACHE_KEY = "market:contracts:master"
_MASTER_CACHE_TTL = 30  # 30 秒

# 从 symbol 提取品种代码的正则:前导字母部分
_CODE_PREFIX_RE = re.compile(r"^[a-zA-Z]+")


def extract_code(symbol: str) -> str:
    """从 symbol 提取品种代码(大写),如 rb2610 → RB,au2612 → AU"""
    m = _CODE_PREFIX_RE.match(symbol)
    return m.group(0).upper() if m else ""


async def _collect_quotes_by_pattern(
    redis_conn: aioredis.Redis,
    pattern: str,
    now: datetime,
) -> dict[str, list[dict[str, Any]]]:
    """按 Redis pattern 扫描 quote，按品种代码聚合（过滤过期合约）"""
    quotes_by_code: dict[str, list[dict[str, Any]]] = {}
    async for key in redis_conn.scan_iter(pattern):
        raw = await redis_conn.get(key)
        if not raw:
            continue
        try:
            quote = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue

        symbol = quote.get("symbol")
        if not symbol:
            continue
        if _is_expired(symbol, now):
            continue

        code = extract_code(symbol)
        if not code:
            continue

        volume = quote.get("volume", 0)
        quotes_by_code.setdefault(code, []).append({
            "symbol": symbol,
            "volume": int(volume) if volume else 0,
        })
    return quotes_by_code


def _extract_year_month(symbol: str) -> tuple[int, int] | None:
    """从 symbol 提取 (年, 月),如 rb2610 → (2026, 10);失败返回 None"""
    digits = "".join(ch for ch in symbol if ch.isdigit())
    if len(digits) < 4:
        return None
    yy = int(digits[-4:-2])
    mm = int(digits[-2:])
    year = 2000 + yy  # 假设 20XX
    return (year, mm)


def _is_expired(symbol: str, now: datetime) -> bool:
    """判断合约是否已过期(年月 < 当前年月)"""
    ym = _extract_year_month(symbol)
    if ym is None:
        return False  # 无法识别的 symbol 不当作过期
    year, month = ym
    current_ym = now.year * 100 + now.month
    symbol_ym = year * 100 + month
    return symbol_ym < current_ym


def resolve_master_for_code(code: str, volume_master: str | None) -> str | None:
    """主力口径对齐（纯函数）：平台主力表优先，量口径兜底

    平台表驱动商品列表/行情订阅/落库主链路，量口径仅在表无该品种
    （如新上市尚未入表）时兜底。get_master_symbol_for_code 对未知
    品种原样返回入参，必须回查 code 确认确有映射，否则会把品种
    代码本身当主力。
    """
    # 入参可能是任意品种形式（RB / rb / rb2610）→ 先规整成品种码再比较
    upper_code = get_code_for_symbol(code) or str(code or "").strip().upper()
    platform = (get_master_symbol_for_code(code) or "").strip().lower()
    if platform and get_code_for_symbol(platform) == upper_code:
        return platform
    return volume_master


async def identify_master_contracts(
    redis_conn: aioredis.Redis,
    now: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    """识别每个品种的主力与次主力合约

    返回结构:
    {
        "RB": {"master": "rb2610", "secondary": "rb2701", "updated_at": "2026-06-23T11:30:00+08:00"},
        "AU": {"master": "au2612", "secondary": None, "updated_at": ...},
        ...
    }
    """
    if now is None:
        now = datetime.now(_BJ_TZ)

    # 优先全月命名空间；为空时回退主行情，避免 monthly 任务未就绪时主力全空
    quotes_by_code: dict[str, list[dict[str, Any]]] = {}
    scanned = await _collect_quotes_by_pattern(redis_conn, "market:monthly:quote:*", now)
    if not scanned:
        scanned = await _collect_quotes_by_pattern(redis_conn, "market:quote:*", now)
    quotes_by_code = scanned

    # 每个品种按 volume 降序排序,取 Top1/Top2
    result: dict[str, dict[str, Any]] = {}
    updated_at = now.isoformat()

    for code, items in quotes_by_code.items():
        # 过滤 volume=0 的(全是 0 时也跳过主力识别)
        active_items = [it for it in items if it["volume"] > 0]
        if not active_items:
            # 全部 volume=0,跳过该品种(返回 null)
            continue

        # 按 volume 降序
        sorted_items = sorted(active_items, key=lambda x: x["volume"], reverse=True)

        # 主力对齐平台表（量口径兜底）；次主力 = 量最大的非主力合约
        # （平台主力缺量数据时旧量主力自然成为 secondary，供换月观察）
        volume_master = sorted_items[0]["symbol"] if sorted_items else None
        master = resolve_master_for_code(code, volume_master)
        secondary = next(
            (it["symbol"] for it in sorted_items if it["symbol"] != master), None
        )

        result[code] = {
            "master": master,
            "secondary": secondary,
            "updated_at": updated_at,
        }

    return result


async def cache_master_contracts(redis_conn: aioredis.Redis) -> dict[str, dict[str, Any]]:
    """识别主力合约并写入 Redis 缓存,TTL 30 秒

    返回识别结果供调用者使用(主要用于日志和测试)。
    """
    result = await identify_master_contracts(redis_conn)
    if result:
        await redis_conn.set(
            _MASTER_CACHE_KEY,
            json.dumps(result, ensure_ascii=False),
            ex=_MASTER_CACHE_TTL,
        )
        logger.info("主力合约识别完成,覆盖 %d 个品种", len(result))
    else:
        # 即使没识别到也清空旧缓存
        await redis_conn.delete(_MASTER_CACHE_KEY)
        logger.warning("主力合约识别无结果,可能 Redis quote 缓存为空")
    return result


async def get_master_contracts(redis_conn: aioredis.Redis) -> dict[str, dict[str, Any]]:
    """从 Redis 读取主力合约缓存,不存在返回空 dict"""
    raw = await redis_conn.get(_MASTER_CACHE_KEY)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("market:contracts:master 缓存解析失败")
        return {}
