"""Skills API 路由 —— 商城浏览、安装管理、Token 配置"""

import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_session
from app.data.skills_marketplace import search_skills, get_leaderboard, get_skill_detail, SkillsAuthError
from app.data.skills_github import search_github_skills, get_github_leaderboard, get_github_skill_detail
from app.models.ai_skill import AISkillConfig
from app.models.user import User
from app.schemas.ai_skill import (
    MarketplaceSkillItem,
    MarketplaceSkillDetail,
    InstalledSkillItem,
    SkillInstallRequest,
    SkillConfigResponse,
    SkillConfigUpdateRequest,
)
from app.services.crypto import decrypt, encrypt, mask_key
from app.services.skill_manager import (
    install_from_marketplace,
    install_from_git_url,
    install_from_upload,
    toggle_skill,
    uninstall_skill,
    list_installed_skills,
)

router = APIRouter(prefix="/api/ai/skills", tags=["AI Skills"])


# ============ 辅助函数 ============


async def _get_user_token(session: AsyncSession, user_id) -> str:
    """获取用户解密后的 skills.sh token，未配置则返回空字符串"""
    stmt = select(AISkillConfig).where(AISkillConfig.user_id == user_id)
    result = await session.execute(stmt)
    config = result.scalar_one_or_none()
    if not config or not config.skills_sh_token:
        return ""
    return decrypt(config.skills_sh_token)


# ============ Token 配置接口 ============


@router.get("/config", response_model=SkillConfigResponse)
async def get_config(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SkillConfigResponse:
    """获取当前用户的 Skills 配置"""
    token = await _get_user_token(session, current_user.id)
    return SkillConfigResponse(
        has_token=bool(token),
        masked_token=mask_key(token) if token else "",
    )


@router.put("/config", response_model=SkillConfigResponse)
async def update_config(
    body: SkillConfigUpdateRequest,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SkillConfigResponse:
    """更新 skills.sh API token"""
    encrypted = encrypt(body.token)
    stmt = select(AISkillConfig).where(AISkillConfig.user_id == current_user.id)
    result = await session.execute(stmt)
    config = result.scalar_one_or_none()
    if config:
        config.skills_sh_token = encrypted
    else:
        config = AISkillConfig(user_id=current_user.id, skills_sh_token=encrypted)
        session.add(config)
    await session.flush()
    return SkillConfigResponse(has_token=True, masked_token=mask_key(body.token))


@router.delete("/config")
async def delete_config(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    """删除 skills.sh API token"""
    stmt = select(AISkillConfig).where(AISkillConfig.user_id == current_user.id)
    result = await session.execute(stmt)
    config = result.scalar_one_or_none()
    if config:
        await session.delete(config)
        await session.flush()
    return {"status": "ok"}


# ============ 商城接口（需要认证以读取用户 token） ============


@router.get("/marketplace/search", response_model=list[MarketplaceSkillItem])
async def marketplace_search(
    q: str = Query(default="", description="搜索关键词"),
    page: int = Query(default=1, ge=1, description="页码"),
    size: int = Query(default=20, ge=1, le=50, description="每页数量"),
    source: str = Query(default="skillsmp", description="数据源: skillsmp | github"),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[MarketplaceSkillItem]:
    """搜索 skills 商城（支持 skillsmp 和 github 两个数据源）"""
    if not q.strip():
        return []
    if source == "github":
        items = await search_github_skills(q, page, size)
    else:
        token = await _get_user_token(session, current_user.id)
        try:
            items = await search_skills(q, page, size, token)
        except SkillsAuthError:
            raise HTTPException(status_code=403, detail="Skillsmp API 需要认证，请在设置中配置 Token")
    return [MarketplaceSkillItem(**item) for item in items]


@router.get("/marketplace/leaderboard", response_model=list[MarketplaceSkillItem])
async def marketplace_leaderboard(
    page: int = Query(default=1, ge=1, description="页码"),
    size: int = Query(default=20, ge=1, le=50, description="每页数量"),
    source: str = Query(default="skillsmp", description="数据源: skillsmp | github"),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[MarketplaceSkillItem]:
    """获取热门 skills 排行榜"""
    if source == "github":
        items = await get_github_leaderboard(page, size)
    else:
        token = await _get_user_token(session, current_user.id)
        try:
            items = await get_leaderboard(page, size, token)
        except SkillsAuthError:
            raise HTTPException(status_code=403, detail="Skillsmp API 需要认证，请在设置中配置 Token")
    return [MarketplaceSkillItem(**item) for item in items]


@router.get("/marketplace/{skill_id}", response_model=MarketplaceSkillDetail)
async def marketplace_detail(
    skill_id: str,
    source: str = Query(default="skillsmp", description="数据源: skillsmp | github"),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> MarketplaceSkillDetail:
    """获取商城 skill 详情"""
    if source == "github":
        detail = await get_github_skill_detail(skill_id)
    else:
        token = await _get_user_token(session, current_user.id)
        detail = await get_skill_detail(skill_id, token)
    if not detail:
        raise HTTPException(status_code=404, detail="skill 未找到或商城暂不可用")
    return MarketplaceSkillDetail(**detail)


# ============ 已安装管理接口 ============


@router.get("/installed", response_model=list[InstalledSkillItem])
async def list_installed(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[InstalledSkillItem]:
    """获取已安装 skills 列表"""
    skills = await list_installed_skills(session, current_user.id)
    return [InstalledSkillItem.model_validate(s) for s in skills]


@router.post("/install", response_model=InstalledSkillItem)
async def install_skill(
    body: SkillInstallRequest,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> InstalledSkillItem:
    """安装 skill（支持商城 ID 或 Git URL）"""
    try:
        if body.marketplace_id:
            skill = await install_from_marketplace(session, current_user.id, body.marketplace_id)
        elif body.git_url:
            git_url = _normalize_git_url(body.git_url)
            skill = await install_from_git_url(session, current_user.id, git_url)
        else:
            raise HTTPException(status_code=400, detail="请提供 marketplace_id 或 git_url")
        return InstalledSkillItem.model_validate(skill)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/upload", response_model=InstalledSkillItem)
async def upload_skill(
    file: UploadFile = File(description="SKILL.md 文件"),
    name: str = Form(description="Skill 名称"),
    trigger_word: str = Form(description="触发词"),
    description: str = Form(default="", description="描述"),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> InstalledSkillItem:
    """上传自定义 SKILL.md 文件"""
    if not file.filename or not file.filename.endswith(".md"):
        raise HTTPException(status_code=400, detail="请上传 .md 文件")

    content = await file.read()
    try:
        skill = await install_from_upload(
            session, current_user.id, content, name, trigger_word, description,
        )
        return InstalledSkillItem.model_validate(skill)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/installed/{skill_id}")
async def delete_installed(
    skill_id: UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    """卸载 skill"""
    try:
        await uninstall_skill(session, current_user.id, skill_id)
        return {"status": "ok"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/installed/{skill_id}/toggle", response_model=InstalledSkillItem)
async def toggle_installed(
    skill_id: UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> InstalledSkillItem:
    """切换 skill 启用/禁用状态"""
    try:
        skill = await toggle_skill(session, current_user.id, skill_id)
        return InstalledSkillItem.model_validate(skill)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ============ 辅助函数 ============


def _normalize_git_url(raw: str) -> str:
    """标准化 git URL，支持 /install github.com/xxx 格式"""
    raw = raw.strip()

    # 处理 /install github.com/user/repo 格式
    install_match = re.match(r"^/install\s+(.+)$", raw)
    if install_match:
        raw = install_match.group(1).strip()

    # 补全 https://
    if raw.startswith("github.com/"):
        raw = f"https://{raw}"

    # 确保 .git 后缀（git clone 需要）
    if not raw.endswith(".git"):
        raw = f"{raw}.git"

    return raw
