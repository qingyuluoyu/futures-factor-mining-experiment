"""skill_manager 单元测试

覆盖安装、卸载、启用/禁用核心逻辑。
使用 mock 替代数据库和文件系统操作。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.services.skill_manager import (
    _sanitize_name,
    _parse_skill_md,
    install_from_upload,
    toggle_skill,
    uninstall_skill,
    list_installed_skills,
    read_skill_md,
)
from app.models.ai_skill import AISkill


def _make_skill(**overrides) -> AISkill:
    """构造测试用 AISkill 实例"""
    defaults = dict(
        id=uuid4(),
        user_id=uuid4(),
        name="test_skill",
        trigger_word="test",
        source="upload",
        source_url="",
        skill_md_path="/tmp/skills/test_skill/SKILL.md",
        description="测试 skill",
        is_enabled=True,
    )
    defaults.update(overrides)
    return AISkill(**defaults)


# ============ _sanitize_name ============


def test_sanitize_name_normal():
    """普通名称保持不变"""
    assert _sanitize_name("my-skill") == "my-skill"


def test_sanitize_name_special_chars():
    """特殊字符替换为下划线"""
    assert _sanitize_name("my skill @#$") == "my_skill____"


def test_sanitize_name_too_long():
    """超长名称截断到 100 字符"""
    long_name = "a" * 200
    assert len(_sanitize_name(long_name)) == 100


# ============ _parse_skill_md ============


def test_parse_skill_md_with_header():
    """解析带标题和触发词的 SKILL.md"""
    content = """# My Awesome Skill

trigger: awesome

This is a description of the skill.
"""
    name, trigger, desc = _parse_skill_md(content, "fallback")
    assert name == "My Awesome Skill"
    assert trigger == "awesome"
    assert "description" in desc.lower()


def test_parse_skill_md_fallback():
    """无法解析时使用默认值"""
    content = "some random content without structure"
    name, trigger, desc = _parse_skill_md(content, "fallback")
    assert name == "fallback"
    assert trigger == "fallback"


# ============ install_from_upload ============


@pytest.mark.asyncio
async def test_install_from_upload_success():
    """上传安装成功"""
    session = AsyncMock()
    user_id = uuid4()

    # mock: 没有同名 skill
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    session.execute.return_value = mock_result

    # mock: flush 和 refresh
    async def mock_refresh(obj):
        obj.id = uuid4()
    session.flush = AsyncMock()
    session.refresh = AsyncMock(side_effect=mock_refresh)

    content = b"# Test Skill\ntrigger: test\nDescription here"
    skill = await install_from_upload(session, user_id, content, "test_skill", "test", "desc")

    assert skill is not None
    session.add.assert_called_once()


@pytest.mark.asyncio
async def test_install_from_upload_too_large():
    """上传文件超过大小限制"""
    session = AsyncMock()
    big_content = b"x" * (101 * 1024)  # 101KB

    with pytest.raises(ValueError, match="超过"):
        await install_from_upload(session, uuid4(), big_content, "big", "big", "")


@pytest.mark.asyncio
async def test_install_from_upload_empty():
    """上传空文件"""
    session = AsyncMock()

    with pytest.raises(ValueError, match="为空"):
        await install_from_upload(session, uuid4(), b"", "empty", "empty", "")


@pytest.mark.asyncio
async def test_install_from_upload_duplicate():
    """上传同名 skill"""
    session = AsyncMock()
    user_id = uuid4()

    # mock: 已存在同名 skill
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = _make_skill(name="dup")
    session.execute.return_value = mock_result

    with pytest.raises(ValueError, match="已存在"):
        await install_from_upload(session, user_id, b"# Dup", "dup", "dup", "")


# ============ toggle_skill ============


@pytest.mark.asyncio
async def test_toggle_skill_enable():
    """切换启用状态"""
    session = AsyncMock()
    user_id = uuid4()
    skill = _make_skill(user_id=user_id, is_enabled=True)

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = skill
    session.execute.return_value = mock_result
    session.flush = AsyncMock()
    session.refresh = AsyncMock()

    result = await toggle_skill(session, user_id, skill.id)
    assert result.is_enabled is False


@pytest.mark.asyncio
async def test_toggle_skill_not_found():
    """切换不存在的 skill"""
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    session.execute.return_value = mock_result

    with pytest.raises(ValueError, match="不存在"):
        await toggle_skill(session, uuid4(), uuid4())


# ============ uninstall_skill ============


@pytest.mark.asyncio
async def test_uninstall_skill_success():
    """卸载成功"""
    session = AsyncMock()
    user_id = uuid4()
    skill = _make_skill(user_id=user_id, skill_md_path="/tmp/nonexistent/SKILL.md")

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = skill
    session.execute.return_value = mock_result
    session.flush = AsyncMock()

    await uninstall_skill(session, user_id, skill.id)
    session.execute.assert_called()


@pytest.mark.asyncio
async def test_uninstall_skill_not_found():
    """卸载不存在的 skill"""
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    session.execute.return_value = mock_result

    with pytest.raises(ValueError, match="不存在"):
        await uninstall_skill(session, uuid4(), uuid4())


# ============ list_installed_skills ============


@pytest.mark.asyncio
async def test_list_installed_skills():
    """查询已安装列表"""
    session = AsyncMock()
    user_id = uuid4()
    skills = [_make_skill(user_id=user_id), _make_skill(user_id=user_id, name="other")]

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = skills
    session.execute.return_value = mock_result

    result = await list_installed_skills(session, user_id)
    assert len(result) == 2


# ============ read_skill_md ============


def test_read_skill_md_not_exists():
    """读取不存在的文件返回空字符串"""
    content = read_skill_md("/tmp/nonexistent_path_abc123/SKILL.md")
    assert content == ""


def test_read_skill_md_exists(tmp_path):
    """读取存在的文件返回内容"""
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("# Hello", encoding="utf-8")
    content = read_skill_md(str(skill_file))
    assert content == "# Hello"
