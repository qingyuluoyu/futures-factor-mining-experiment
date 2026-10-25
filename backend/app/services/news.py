"""新闻采集服务 —— 采集 + 关键词匹配 + Redis 缓存写入"""

import json
import logging

import redis.asyncio as aioredis

from app.data.contract_keywords import find_matching_symbols
from app.data.news_fetcher import fetch_futures_news
from app.schemas.news import NewsItem

logger = logging.getLogger("qihuo.news_service")

# Redis 缓存 key
_KEY_LATEST = "news:latest"
_KEY_SYMBOL_PREFIX = "news:symbol:"

# 缓存配置
_MAX_LATEST = 200
_MAX_PER_SYMBOL = 50
_CACHE_TTL = 1800  # 30 分钟


async def collect_and_cache_news(redis_conn: aioredis.Redis) -> None:
    """采集新闻、匹配品种、写入 Redis 缓存"""
    news_list = await fetch_futures_news(page_size=50)
    if not news_list:
        logger.info("本次未采集到新闻，跳过缓存更新")
        return

    # 关键词匹配：为每条新闻填充 symbols
    for item in news_list:
        item.symbols = find_matching_symbols(item.title)

    logger.info("采集到 %d 条新闻，开始写入缓存", len(news_list))

    # 管道批量写入
    pipe = redis_conn.pipeline()

    # 全量缓存（先删除旧数据，再按时间顺序写入，最新在最前）
    pipe.delete(_KEY_LATEST)
    for item in news_list:
        pipe.rpush(_KEY_LATEST, item.model_dump_json(ensure_ascii=False))
    pipe.expire(_KEY_LATEST, _CACHE_TTL)

    # 品种缓存（每个品种最多 50 条）
    symbol_items: dict[str, list[NewsItem]] = {}
    for item in news_list:
        for symbol in item.symbols:
            items = symbol_items.get(symbol, [])
            items.append(item)
            symbol_items[symbol] = items

    for symbol, items in symbol_items.items():
        key = f"{_KEY_SYMBOL_PREFIX}{symbol}"
        pipe.delete(key)
        for item in items:
            pipe.rpush(key, item.model_dump_json(ensure_ascii=False))
        pipe.expire(key, _CACHE_TTL)

    await pipe.execute()
    logger.info("新闻缓存写入完成，涉及 %d 个品种", len(symbol_items))


async def get_latest_news(redis_conn: aioredis.Redis, limit: int = 20) -> list[dict]:
    """获取全量最新新闻（从 Redis 读取）"""
    raw_list = await redis_conn.lrange(_KEY_LATEST, 0, limit - 1)
    return [json.loads(item) for item in raw_list]


async def get_symbol_news(redis_conn: aioredis.Redis, symbol: str, limit: int = 50) -> list[dict]:
    """获取指定品种关联新闻"""
    key = f"{_KEY_SYMBOL_PREFIX}{symbol}"
    raw_list = await redis_conn.lrange(key, 0, limit - 1)
    return [json.loads(item) for item in raw_list]
