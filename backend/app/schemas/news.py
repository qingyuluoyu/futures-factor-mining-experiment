"""新闻数据 Pydantic 模型"""

from pydantic import BaseModel


class NewsItem(BaseModel):
    """单条新闻"""
    title: str
    source: str
    url: str
    time: str
    symbols: list[str]


class ArticleContent(BaseModel):
    """文章正文内容"""
    title: str
    content: str
    source_url: str
