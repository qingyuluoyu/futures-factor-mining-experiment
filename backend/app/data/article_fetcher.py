"""文章正文抓取 —— 从外部 URL 提取文章标题和正文"""

import logging
import re
from html.parser import HTMLParser

import httpx

from app.schemas.news import ArticleContent

logger = logging.getLogger("qihuo.article_fetcher")

_TIMEOUT = 10
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


class _ArticleExtractor(HTMLParser):
    """从 HTML 中提取标题和正文段落"""

    # 不需要内容的标签
    _SKIP_TAGS = frozenset({"script", "style", "nav", "header", "footer", "aside", "iframe", "noscript"})

    # 正文容器标签 / class 关键词
    _BODY_TAGS = frozenset({"article", "main"})
    _BODY_CLASSES = frozenset({"article", "content", "body", "detail", "text", "news-content", "post-content"})

    def __init__(self) -> None:
        super().__init__()
        self.title: str = ""
        self._in_title: bool = False
        self._skip_depth: int = 0
        self._skip_tag: str = ""
        self._in_body: bool = False
        self._body_depth: int = 0
        self._paragraphs: list[str] = []
        self._current_p: list[str] = []
        self._in_p: bool = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        attr_dict = dict(attrs)

        # <title> 标签
        if tag_lower == "title":
            self._in_title = True
            return

        # 跳过不需要的标签
        if tag_lower in self._SKIP_TAGS:
            self._skip_depth += 1
            self._skip_tag = tag_lower
            return

        # 检测正文容器
        if tag_lower in self._BODY_TAGS:
            self._in_body = True
            self._body_depth += 1
            return

        class_val = (attr_dict.get("class") or "").lower()
        if any(cls in class_val for cls in self._BODY_CLASSES):
            self._in_body = True
            self._body_depth += 1
            return

        # 段落标签
        if tag_lower == "p":
            self._in_p = True
            self._current_p = []

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()

        if tag_lower == "title":
            self._in_title = False
            return

        if self._skip_depth > 0:
            self._skip_depth -= 1
            return

        if self._in_body and self._body_depth > 0:
            if tag_lower in self._BODY_TAGS:
                self._body_depth -= 1
                if self._body_depth == 0:
                    self._in_body = False
            return

        if tag_lower == "p" and self._in_p:
            self._in_p = False
            text = " ".join("".join(self._current_p).split()).strip()
            if text:
                self._paragraphs.append(text)

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._in_title:
            self.title += data
            return
        if self._in_p:
            self._current_p.append(data)

    @property
    def content(self) -> str:
        """返回拼接后的正文段落"""
        return "\n\n".join(self._paragraphs)


def _extract_article(html: str) -> tuple[str, str]:
    """从 HTML 中提取标题和正文"""
    extractor = _ArticleExtractor()
    extractor.feed(html)

    title = extractor.title.strip()

    # 如果 HTMLParser 没有提取到段落，用正则兜底提取 <p> 标签内容
    content = extractor.content
    if not content:
        p_texts = re.findall(r"<p[^>]*>(.*?)</p>", html, re.S | re.I)
        clean_texts: list[str] = []
        for p in p_texts:
            text = re.sub(r"<[^>]+>", "", p).strip()
            text = " ".join(text.split())
            if text and len(text) > 10:
                clean_texts.append(text)
        content = "\n\n".join(clean_texts)

    # 清理标题中的 HTML 实体和多余后缀
    title = re.sub(r"\s*[-_|].*$", "", title).strip()
    if not title:
        title = "新闻详情"

    return title, content


async def fetch_article_content(url: str) -> ArticleContent:
    """抓取外部文章页面并提取正文内容"""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
    except (httpx.HTTPError, Exception) as e:
        logger.error("文章页面抓取失败 (%s): %s", url, e)
        return ArticleContent(title="加载失败", content="无法获取文章内容，请稍后重试。", source_url=url)

    # 检测编码
    html = response.text
    title, content = _extract_article(html)

    if not content:
        content = "未能提取到文章正文内容。"

    return ArticleContent(title=title, content=content, source_url=url)
