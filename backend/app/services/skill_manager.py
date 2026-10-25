"""Skill 安装与管理服务 —— 安装、卸载、启用/禁用、列表查询"""

import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.skills_marketplace import get_skill_detail
from app.models.ai_skill import AISkill

logger = logging.getLogger(__name__)

# skills 本地存储根目录（相对于 backend/）
_SKILLS_ROOT = Path(__file__).resolve().parents[2] / "skills"
_MAX_SKILL_MD_SIZE = 100 * 1024  # 100KB


def _skill_dir(user_id: UUID, skill_name: str) -> Path:
    """获取用户 skill 的本地存储目录"""
    return _SKILLS_ROOT / str(user_id) / skill_name


def _sanitize_name(name: str) -> str:
    """清理 skill 名称，只保留安全字符"""
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", name)[:100]


async def install_from_marketplace(
    session: AsyncSession,
    user_id: UUID,
    marketplace_id: str,
) -> AISkill:
    """从商城安装 skill

    Args:
        session: 数据库会话
        user_id: 用户 ID
        marketplace_id: 商城 skill ID

    Returns:
        安装后的 AISkill 记录

    Raises:
        ValueError: skill 已安装或安装失败
    """
    # 检查是否已安装（按 source_url 判断）
    existing = await session.execute(
        select(AISkill).where(
            AISkill.user_id == user_id,
            AISkill.source_url == marketplace_id,
        )
    )
    if existing.scalar_one_or_none():
        raise ValueError("该 skill 已安装")

    # 从商城获取详情
    detail = await get_skill_detail(marketplace_id)
    if not detail:
        raise ValueError("无法获取 skill 详情，商城暂不可用")

    git_url = detail.get("git_url", "")
    name = _sanitize_name(detail.get("name", f"skill_{marketplace_id}"))
    trigger_word = detail.get("trigger_word", name)
    description = detail.get("description", "")[:500]
    skill_md_content = detail.get("skill_md_preview", "")

    # 如果有 git_url，尝试克隆获取完整 SKILL.md
    if git_url:
        skill_md_content = await _clone_and_read_skill_md(git_url, name, user_id)
    elif skill_md_content:
        # 直接从商城详情中保存
        skill_md_content = skill_md_content

    if not skill_md_content:
        raise ValueError("无法获取 SKILL.md 内容")

    # 保存到本地文件
    skill_dir = _skill_dir(user_id, name)
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md_path = skill_dir / "SKILL.md"
    skill_md_path.write_text(skill_md_content, encoding="utf-8")

    # 写入数据库
    skill = AISkill(
        user_id=user_id,
        name=name,
        trigger_word=trigger_word,
        source="marketplace",
        source_url=marketplace_id,
        skill_md_path=str(skill_md_path),
        description=description,
        is_enabled=True,
    )
    session.add(skill)
    await session.flush()
    await session.refresh(skill)
    logger.info("skill 安装成功: user=%s, name=%s", user_id, name)
    return skill


async def install_from_git_url(
    session: AsyncSession,
    user_id: UUID,
    git_url: str,
) -> AISkill:
    """从 GitHub 仓库地址安装 skill

    Args:
        session: 数据库会话
        user_id: 用户 ID
        git_url: GitHub 仓库地址

    Returns:
        安装后的 AISkill 记录

    Raises:
        ValueError: 安装失败
    """
    # 从 URL 提取仓库名作为 skill 名称
    parts = git_url.rstrip("/").split("/")
    repo_name = parts[-1].replace(".git", "") if parts else "unknown_skill"
    name = _sanitize_name(repo_name)

    # 检查是否已安装
    existing = await session.execute(
        select(AISkill).where(
            AISkill.user_id == user_id,
            AISkill.source_url == git_url,
        )
    )
    if existing.scalar_one_or_none():
        raise ValueError("该 skill 已安装")

    # 克隆并读取 SKILL.md
    skill_md_content = await _clone_and_read_skill_md(git_url, name, user_id)
    if not skill_md_content:
        raise ValueError("无法从仓库获取 SKILL.md 内容")

    # 从 SKILL.md 解析名称和触发词
    parsed_name, trigger_word, description = _parse_skill_md(skill_md_content, name)

    # 保存到本地
    skill_dir = _skill_dir(user_id, parsed_name)
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md_path = skill_dir / "SKILL.md"
    skill_md_path.write_text(skill_md_content, encoding="utf-8")

    # 写入数据库
    skill = AISkill(
        user_id=user_id,
        name=parsed_name,
        trigger_word=trigger_word,
        source="git_url",
        source_url=git_url,
        skill_md_path=str(skill_md_path),
        description=description,
        is_enabled=True,
    )
    session.add(skill)
    await session.flush()
    await session.refresh(skill)
    logger.info("skill 从 git 安装成功: user=%s, name=%s", user_id, parsed_name)
    return skill


async def install_from_upload(
    session: AsyncSession,
    user_id: UUID,
    file_content: bytes,
    name: str,
    trigger_word: str,
    description: str,
) -> AISkill:
    """从手动上传的 SKILL.md 安装 skill

    Args:
        session: 数据库会话
        user_id: 用户 ID
        file_content: SKILL.md 文件内容
        name: skill 名称
        trigger_word: 触发词
        description: 描述

    Returns:
        安装后的 AISkill 记录

    Raises:
        ValueError: 文件无效或 skill 已存在
    """
    if len(file_content) > _MAX_SKILL_MD_SIZE:
        raise ValueError("SKILL.md 文件大小超过 100KB 限制")

    content_str = file_content.decode("utf-8", errors="replace")
    if not content_str.strip():
        raise ValueError("SKILL.md 文件内容为空")

    safe_name = _sanitize_name(name)

    # 检查是否已存在同名 skill
    existing = await session.execute(
        select(AISkill).where(
            AISkill.user_id == user_id,
            AISkill.name == safe_name,
        )
    )
    if existing.scalar_one_or_none():
        raise ValueError(f"已存在同名 skill: {safe_name}")

    # 保存到本地
    skill_dir = _skill_dir(user_id, safe_name)
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md_path = skill_dir / "SKILL.md"
    skill_md_path.write_text(content_str, encoding="utf-8")

    # 写入数据库
    skill = AISkill(
        user_id=user_id,
        name=safe_name,
        trigger_word=trigger_word,
        source="upload",
        source_url="",
        skill_md_path=str(skill_md_path),
        description=description[:500],
        is_enabled=True,
    )
    session.add(skill)
    await session.flush()
    await session.refresh(skill)
    logger.info("skill 上传安装成功: user=%s, name=%s", user_id, safe_name)
    return skill


async def toggle_skill(
    session: AsyncSession,
    user_id: UUID,
    skill_id: UUID,
) -> AISkill:
    """切换 skill 启用/禁用状态

    Args:
        session: 数据库会话
        user_id: 用户 ID
        skill_id: skill ID

    Returns:
        更新后的 AISkill 记录

    Raises:
        ValueError: skill 不存在
    """
    stmt = select(AISkill).where(AISkill.id == skill_id, AISkill.user_id == user_id)
    result = await session.execute(stmt)
    skill = result.scalar_one_or_none()
    if not skill:
        raise ValueError("skill 不存在")

    skill.is_enabled = not skill.is_enabled
    await session.flush()
    await session.refresh(skill)
    return skill


async def uninstall_skill(
    session: AsyncSession,
    user_id: UUID,
    skill_id: UUID,
) -> None:
    """卸载 skill：删除本地文件 + 数据库记录

    Args:
        session: 数据库会话
        user_id: 用户 ID
        skill_id: skill ID

    Raises:
        ValueError: skill 不存在
    """
    stmt = select(AISkill).where(AISkill.id == skill_id, AISkill.user_id == user_id)
    result = await session.execute(stmt)
    skill = result.scalar_one_or_none()
    if not skill:
        raise ValueError("skill 不存在")

    # 删除本地文件
    skill_md_path = Path(skill.skill_md_path)
    if skill_md_path.exists():
        parent = skill_md_path.parent
        shutil.rmtree(parent, ignore_errors=True)
        # 清理空的父目录
        user_dir = parent.parent
        if user_dir.exists() and not any(user_dir.iterdir()):
            user_dir.rmdir()

    # 删除数据库记录
    await session.execute(sa_delete(AISkill).where(AISkill.id == skill_id))
    await session.flush()
    logger.info("skill 卸载成功: user=%s, skill_id=%s", user_id, skill_id)


async def list_installed_skills(
    session: AsyncSession,
    user_id: UUID,
) -> list[AISkill]:
    """查询用户已安装的所有 skills

    Args:
        session: 数据库会话
        user_id: 用户 ID

    Returns:
        已安装 skills 列表
    """
    stmt = (
        select(AISkill)
        .where(AISkill.user_id == user_id)
        .order_by(AISkill.created_at.desc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_enabled_skills(
    session: AsyncSession,
    user_id: UUID,
) -> list[AISkill]:
    """查询用户已启用的 skills（用于 AI 工具注册）

    Args:
        session: 数据库会话
        user_id: 用户 ID

    Returns:
        已启用的 skills 列表，最多 20 个
    """
    stmt = (
        select(AISkill)
        .where(AISkill.user_id == user_id, AISkill.is_enabled.is_(True))
        .order_by(AISkill.created_at.desc())
        .limit(20)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


def read_skill_md(skill_md_path: str) -> str:
    """读取 SKILL.md 文件内容

    Args:
        skill_md_path: 本地文件路径

    Returns:
        SKILL.md 文件内容
    """
    path = Path(skill_md_path)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


# ============ 内部辅助函数 ============


async def _clone_and_read_skill_md(git_url: str, name: str, user_id: UUID) -> str:
    """克隆仓库并读取 SKILL.md 内容

    Args:
        git_url: Git 仓库地址
        name: skill 名称
        user_id: 用户 ID

    Returns:
        SKILL.md 内容，失败返回空字符串
    """
    import asyncio

    target_dir = _skill_dir(user_id, name)

    # 如果目录已存在，先删除
    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)

    with tempfile.TemporaryDirectory() as tmp_dir:
        clone_cmd = [
            "git", "clone", "--depth", "1",
            git_url,
            str(tmp_dir + "/repo"),
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *clone_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0:
                logger.warning("git clone 失败: %s", stderr.decode(errors="replace")[:200])
                return ""
        except asyncio.TimeoutError:
            logger.warning("git clone 超时: %s", git_url)
            return ""
        except Exception as e:
            logger.warning("git clone 异常: %s", str(e)[:200])
            return ""

        # 查找 SKILL.md
        repo_path = Path(tmp_dir) / "repo"
        skill_md = _find_skill_md(repo_path)
        if not skill_md:
            logger.warning("仓库中未找到 SKILL.md: %s", git_url)
            return ""

        return skill_md.read_text(encoding="utf-8")


def _find_skill_md(repo_path: Path) -> Path | None:
    """在仓库目录中查找 SKILL.md 文件"""
    # 优先查找根目录
    root_md = repo_path / "SKILL.md"
    if root_md.exists():
        return root_md

    # 递归查找（最多 2 层）
    for md_file in repo_path.rglob("SKILL.md"):
        depth = len(md_file.relative_to(repo_path).parts) - 1
        if depth <= 2:
            return md_file

    # 也尝试小写
    root_md_lower = repo_path / "skill.md"
    if root_md_lower.exists():
        return root_md_lower

    return None


def _parse_skill_md(content: str, fallback_name: str) -> tuple[str, str, str]:
    """从 SKILL.md 内容解析名称、触发词和描述

    Args:
        content: SKILL.md 文件内容
        fallback_name: 解析失败时使用的默认名称

    Returns:
        (name, trigger_word, description) 元组
    """
    name = fallback_name
    trigger_word = fallback_name
    description = ""

    lines = content.strip().split("\n")
    for line in lines[:20]:
        # 解析名称（第一行标题）
        if line.startswith("# ") and name == fallback_name:
            name = line[2:].strip()

        # 解析触发词
        lower = line.lower()
        if "trigger" in lower and ":" in line:
            trigger_word = line.split(":", 1)[1].strip().strip("`\"'")

        # 解析描述
        if not description and line.strip() and not line.startswith("#") and ":" not in lower:
            if len(line.strip()) > 10:
                description = line.strip()[:200]

    return name, trigger_word, description
