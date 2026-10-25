"""用户 AI 工具开关 —— Redis 持久化禁用列表 + 自由交易

关闭后：网关不下发给模型，且 execute_tool 拒绝执行。
自由交易：需确认的写操作可跳过确认直接执行。
默认全部启用、自由交易关闭。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.services.ai.providers.base import ToolDef
from app.services.ai.tools.catalog import known_tool_names
from app.services.ai.tools.definitions import get_tool_definitions

logger = logging.getLogger("qihuo.ai.tool_prefs")

_KEY_DISABLED = "ai:tools:disabled"
_KEY_FREE_TRADE = "ai:tools:free_trade"


def _disabled_key(user_id: str) -> str:
    return f"{_KEY_DISABLED}:{user_id}"


def _free_trade_key(user_id: str) -> str:
    return f"{_KEY_FREE_TRADE}:{user_id}"


def _parse_name_set(raw: Any, known: set[str]) -> set[str]:
    """解析 Redis JSON 列表为工具名集合"""
    if not raw:
        return set()
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
        if not isinstance(data, list):
            return set()
        return {str(x) for x in data if str(x) in known}
    except Exception:
        return set()


async def get_disabled_tools(
    redis_conn: Any | None,
    user_id: str,
) -> set[str]:
    """读取用户已禁用的工具名集合"""
    if redis_conn is None or not user_id:
        return set()
    try:
        raw = await redis_conn.get(_disabled_key(user_id))
    except Exception:
        logger.exception("读取工具开关失败 user=%s", user_id)
        return set()
    return _parse_name_set(raw, known_tool_names())


async def get_free_trade_tools(
    redis_conn: Any | None,
    user_id: str,
) -> set[str]:
    """读取已开启「自由交易」的工具名（跳过确认）"""
    if redis_conn is None or not user_id:
        return set()
    try:
        raw = await redis_conn.get(_free_trade_key(user_id))
    except Exception:
        logger.exception("读取自由交易开关失败 user=%s", user_id)
        return set()
    # 仅允许原本需确认的工具
    confirmable = {
        t.name for t in get_tool_definitions() if t.requires_confirmation
    }
    return _parse_name_set(raw, confirmable)


async def set_tool_enabled(
    redis_conn: Any | None,
    user_id: str,
    tool_name: str,
    enabled: bool,
) -> set[str]:
    """设置单个工具启用/禁用，返回最新禁用集合"""
    if redis_conn is None:
        raise RuntimeError("Redis 不可用，无法保存工具开关")
    known = known_tool_names()
    if tool_name not in known:
        raise ValueError(f"未知工具: {tool_name}")

    disabled = await get_disabled_tools(redis_conn, user_id)
    if enabled:
        disabled.discard(tool_name)
    else:
        disabled.add(tool_name)

    await redis_conn.set(
        _disabled_key(user_id),
        json.dumps(sorted(disabled), ensure_ascii=False),
    )
    return disabled


async def set_tool_free_trade(
    redis_conn: Any | None,
    user_id: str,
    tool_name: str,
    free_trade: bool,
) -> set[str]:
    """设置工具自由交易；仅 requires_confirmation 工具有效"""
    if redis_conn is None:
        raise RuntimeError("Redis 不可用，无法保存自由交易开关")
    confirmable = {
        t.name for t in get_tool_definitions() if t.requires_confirmation
    }
    if tool_name not in confirmable:
        raise ValueError(f"工具 {tool_name} 不支持自由交易")

    free_set = await get_free_trade_tools(redis_conn, user_id)
    if free_trade:
        free_set.add(tool_name)
    else:
        free_set.discard(tool_name)

    await redis_conn.set(
        _free_trade_key(user_id),
        json.dumps(sorted(free_set), ensure_ascii=False),
    )
    return free_set


async def get_enabled_tool_definitions(
    redis_conn: Any | None,
    user_id: str,
) -> list[ToolDef]:
    """返回用户已启用的工具；自由交易工具取消需确认标记"""
    disabled = await get_disabled_tools(redis_conn, user_id)
    free_trade = await get_free_trade_tools(redis_conn, user_id)
    result: list[ToolDef] = []
    for tool in get_tool_definitions():
        if tool.name in disabled:
            continue
        if tool.requires_confirmation and tool.name in free_trade:
            # 自由交易：模型无需 confirmed，直接执行
            desc = (
                f"{tool.description}"
                "【当前为自由交易模式，无需用户确认，将直接执行】"
            )
            result.append(
                ToolDef(
                    name=tool.name,
                    description=desc,
                    parameters=tool.parameters,
                    requires_confirmation=False,
                )
            )
        else:
            result.append(tool)
    return result


async def is_tool_enabled(
    redis_conn: Any | None,
    user_id: str | None,
    tool_name: str,
) -> bool:
    """工具是否可用（未知 skill 工具默认可用）"""
    if not user_id:
        return True
    known = known_tool_names()
    if tool_name not in known:
        return True
    disabled = await get_disabled_tools(redis_conn, user_id)
    return tool_name not in disabled
