"""东方财富期货新闻 API 封装

从东方财富公开接口获取期货市场新闻，
返回标准化的新闻列表。
"""

import json
import logging
from datetime import datetime
from urllib.parse import quote

import httpx

from app.schemas.news import NewsItem

logger = logging.getLogger("qihuo.news_fetcher")

# 东方财富期货新闻 API
_NEWS_API_URL = "https://search-api-web.eastmoney.com/search/jsonp"

# 请求超时（秒）
_TIMEOUT = 10

# 通用请求头（东方财富 API 需要浏览器 User-Agent）
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.eastmoney.com/",
}


async def _fetch_news(keyword: str, page_size: int) -> list[NewsItem]:
    """调用东方财富搜索 API，按关键词拉取新闻"""
    param_json = json.dumps({
        "uid": "",
        "keyword": keyword,
        "type": ["cmsArticleWebOld"],
        "client": "web",
        "clientVersion": "1.0.0",
        "param": {
            "cmsArticleWebOld": {
                "searchScope": "default",
                "sort": "time",
                "pageIndex": 1,
                "pageSize": page_size,
                "preTag": "",
                "postTag": "",
            }
        },
    }, ensure_ascii=False)

    url = f"{_NEWS_API_URL}?cb=cb&param={quote(param_json)}"

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
    except (httpx.HTTPError, Exception) as e:
        logger.error("东方财富新闻 API 调用失败 (keyword=%s): %s", keyword, e)
        return []

    try:
        text = response.text
        json_start = text.index("(") + 1
        json_end = text.rindex(")")
        data = json.loads(text[json_start:json_end])
    except (ValueError, KeyError, Exception) as e:
        logger.error("东方财富新闻响应解析失败 (keyword=%s): %s", keyword, e)
        return []

    items = data.get("result", {}).get("cmsArticleWebOld", [])
    if isinstance(items, dict):
        items = items.get("list", [])

    news_list: list[NewsItem] = []
    for item in items:
        title = item.get("title", "").replace("<em>", "").replace("</em>", "")
        if not title:
            continue

        raw_time = item.get("date", "")
        try:
            dt = datetime.strptime(raw_time, "%Y-%m-%d %H:%M:%S")
            display_time = dt.strftime("%m-%d %H:%M")
        except (ValueError, TypeError):
            display_time = ""

        url = item.get("url", "")
        source = item.get("mediaName", item.get("source", "东方财富"))

        news_list.append(NewsItem(
            title=title,
            source=source,
            url=url,
            time=display_time,
            symbols=[],
        ))

    return news_list


async def fetch_futures_news(page_size: int = 50) -> list[NewsItem]:
    """获取期货市场全量新闻（关键词"期货"）"""
    return await _fetch_news("期货", page_size)


async def fetch_news_by_keyword(keyword: str, page_size: int = 20) -> list[NewsItem]:
    """按品种关键词实时搜索新闻（如"螺纹钢""黄金"）"""
    return await _fetch_news(keyword, page_size)
