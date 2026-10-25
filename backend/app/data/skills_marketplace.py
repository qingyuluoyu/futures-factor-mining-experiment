"""Skills 商城客户端 —— 通过 skillsmp.com API 搜索 Skills"""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://skillsmp.com/api/v1"
_TIMEOUT = 10.0


class SkillsAuthError(Exception):
    """商城 API 认证失败"""
    pass


def _headers(token: str = "") -> dict[str, str]:
    """构造请求头，token 为 skillsmp.com API key（可选）"""
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def search_skills(keyword: str, page: int, size: int, token: str = "") -> list[dict[str, Any]]:
    """搜索 skills 商城

    Args:
        keyword: 搜索关键词
        page: 页码（从 1 开始）
        size: 每页数量
        token: 可选 API key
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{_BASE_URL}/skills/search",
                params={"q": keyword, "page": page, "limit": size, "sortBy": "stars"},
                headers=_headers(token),
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("data", {}).get("skills", [])
            return [_normalize_item(item) for item in items]
    except httpx.TimeoutException:
        logger.warning("skillsmp 搜索超时: keyword=%s", keyword)
        return []
    except httpx.HTTPStatusError as e:
        logger.warning("skillsmp 搜索失败: status=%d", e.response.status_code)
        return []
    except Exception as e:
        logger.warning("skillsmp 搜索异常: %s", str(e)[:200])
        return []


async def get_leaderboard(page: int, size: int, token: str = "") -> list[dict[str, Any]]:
    """获取热门 skills 排行榜

    用通用关键词搜索并按 stars 排序获取热门 skills。
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{_BASE_URL}/skills/search",
                params={"q": "ai", "page": page, "limit": size, "sortBy": "stars"},
                headers=_headers(token),
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("data", {}).get("skills", [])
            return [_normalize_item(item) for item in items]
    except httpx.TimeoutException:
        logger.warning("skillsmp 排行榜超时")
        return []
    except httpx.HTTPStatusError as e:
        logger.warning("skillsmp 排行榜失败: status=%d", e.response.status_code)
        return []
    except Exception as e:
        logger.warning("skillsmp 排行榜异常: %s", str(e)[:200])
        return []


async def get_skill_detail(skill_id: str, token: str = "") -> dict[str, Any] | None:
    """获取 skill 详情

    Args:
        skill_id: skillsmp 的 skill ID
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            # skillsmp 没有单独的详情端点，通过搜索 ID 获取
            # skill_id 格式: "author-repo-...-skill-md"
            # 尝试从 skillUrl 获取详情页
            resp = await client.get(
                f"https://skillsmp.com/skills/{skill_id}",
                headers={"Accept": "text/html"},
            )
            # 如果没有详情 API，构造基本详情
            return None
    except Exception as e:
        logger.warning("skillsmp 详情异常: %s", str(e)[:200])
        return None


def _normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    """标准化商城列表项字段"""
    return {
        "id": item.get("id", ""),
        "name": item.get("name", ""),
        "author": item.get("author", ""),
        "description": item.get("description", ""),
        "install_count": item.get("stars", 0),
        "trigger_word": "",
        "git_url": item.get("githubUrl", ""),
    }
