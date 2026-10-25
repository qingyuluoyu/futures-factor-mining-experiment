"""新闻 API —— 提供最新新闻、品种关联新闻和文章正文代理接口"""

from urllib.parse import urlparse

from fastapi import APIRouter, Query, Request

from app.data.article_fetcher import fetch_article_content
from app.data.contract_keywords import extract_base_symbol, get_primary_keyword
from app.data.news_fetcher import fetch_news_by_keyword
from app.schemas.news import ArticleContent
from app.services.news import get_latest_news, get_symbol_news

router = APIRouter(prefix="/api/news", tags=["新闻"])


@router.get("/latest")
async def latest_news(request: Request) -> list[dict]:
    """获取最近 20 条全量新闻"""
    redis_conn = request.app.state.redis
    return await get_latest_news(redis_conn, limit=20)


@router.get("/article")
async def article_content(url: str = Query(..., description="文章 URL")) -> ArticleContent:
    """代理获取外部文章正文内容"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return ArticleContent(title="无效链接", content="不支持该类型的链接。", source_url=url)
    return await fetch_article_content(url)


@router.get("/{symbol}")
async def symbol_news(symbol: str, request: Request) -> list[dict]:
    """获取指定品种关联新闻，缓存为空时实时搜索品种关键词"""
    redis_conn = request.app.state.redis
    base = extract_base_symbol(symbol)

    # 优先从 Redis 缓存读取
    result = await get_symbol_news(redis_conn, base, limit=50)
    if result:
        return result

    # 缓存为空 → 用品种主关键词实时搜索东方财富 API
    keyword = get_primary_keyword(base)
    if keyword:
        items = await fetch_news_by_keyword(keyword, page_size=20)
        result = [item.model_dump() for item in items]

    return result
