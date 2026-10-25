"""GitHub Skills 商城客户端 —— 通过 GitHub API 搜索含 SKILL.md 的仓库"""

import base64
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
_TIMEOUT = 10.0


def _github_headers() -> dict[str, str]:
    """构造 GitHub API 请求头"""
    headers: dict[str, str] = {"Accept": "application/vnd.github.v3+json"}
    return headers


async def search_github_skills(keyword: str, page: int, size: int) -> list[dict[str, Any]]:
    """通过 GitHub 搜索含 skill 关键词的仓库"""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{_GITHUB_API}/search/repositories",
                params={
                    "q": f"{keyword} SKILL.md in:readme",
                    "sort": "stars",
                    "order": "desc",
                    "per_page": size,
                    "page": page,
                },
                headers=_github_headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return [_github_repo_to_item(repo) for repo in data.get("items", [])]
    except httpx.TimeoutException:
        logger.warning("GitHub 搜索超时: keyword=%s", keyword)
        return []
    except httpx.HTTPStatusError as e:
        logger.warning("GitHub 搜索失败: status=%d", e.response.status_code)
        return []
    except Exception as e:
        logger.warning("GitHub 搜索异常: %s", str(e)[:200])
        return []


async def get_github_leaderboard(page: int, size: int) -> list[dict[str, Any]]:
    """获取 GitHub 热门 skill 仓库（按 stars 排序）"""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{_GITHUB_API}/search/repositories",
                params={
                    "q": "SKILL.md in:readme",
                    "sort": "stars",
                    "order": "desc",
                    "per_page": size,
                    "page": page,
                },
                headers=_github_headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return [_github_repo_to_item(repo) for repo in data.get("items", [])]
    except httpx.TimeoutException:
        logger.warning("GitHub 排行榜超时")
        return []
    except httpx.HTTPStatusError as e:
        logger.warning("GitHub 排行榜失败: status=%d", e.response.status_code)
        return []
    except Exception as e:
        logger.warning("GitHub 排行榜异常: %s", str(e)[:200])
        return []


async def get_github_skill_detail(skill_id: str) -> dict[str, Any] | None:
    """获取 GitHub 仓库详情和 SKILL.md 内容

    Args:
        skill_id: GitHub 仓库全名 (owner/repo)
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            repo_resp = await client.get(
                f"{_GITHUB_API}/repos/{skill_id}",
                headers=_github_headers(),
            )
            repo_resp.raise_for_status()
            repo = repo_resp.json()

            # 获取 SKILL.md 内容
            skill_md = ""
            try:
                md_resp = await client.get(
                    f"{_GITHUB_API}/repos/{skill_id}/contents/SKILL.md",
                    headers=_github_headers(),
                )
                if md_resp.status_code == 200:
                    md_data = md_resp.json()
                    if md_data.get("encoding") == "base64" and md_data.get("content"):
                        skill_md = base64.b64decode(md_data["content"]).decode("utf-8")
            except Exception:
                pass

            return {
                "id": repo.get("full_name", ""),
                "name": repo.get("name", ""),
                "author": repo.get("owner", {}).get("login", ""),
                "description": repo.get("description", "") or "",
                "skill_md_preview": skill_md[:2000],
                "install_count": repo.get("stargazers_count", 0),
                "trigger_word": "",
                "git_url": repo.get("clone_url", ""),
            }
    except httpx.TimeoutException:
        logger.warning("GitHub 详情超时: skill_id=%s", skill_id)
        return None
    except httpx.HTTPStatusError as e:
        logger.warning("GitHub 详情失败: skill_id=%s, status=%d", skill_id, e.response.status_code)
        return None
    except Exception as e:
        logger.warning("GitHub 详情异常: %s", str(e)[:200])
        return None


def _github_repo_to_item(repo: dict[str, Any]) -> dict[str, Any]:
    """GitHub 仓库对象转商城列表项"""
    return {
        "id": repo.get("full_name", ""),
        "name": repo.get("name", ""),
        "author": repo.get("owner", {}).get("login", ""),
        "description": repo.get("description", "") or "",
        "install_count": repo.get("stargazers_count", 0),
        "trigger_word": ", ".join(repo.get("topics", [])[:3]),
        "git_url": repo.get("clone_url", ""),
    }
