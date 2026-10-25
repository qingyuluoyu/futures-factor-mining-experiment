"""Agent 工具执行分发"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ai.providers.base import ToolDef
from app.services.ai.execution_context import ExecutionContext
from app.services.ai.tools.safety import CONFIRMATION_UNAVAILABLE, MODE_UNAWARE_TOOLS, requires_server_confirmation
from app.services.ai.tools.definitions import get_tool_definitions, get_tool_map
from app.services.ai.indicator_settings import (
    tool_get_indicator_settings,
    tool_update_indicator_settings,
)
from app.services.ai.tools.indicator import tool_calculate_indicator
from app.services.ai.tools.market import (
    tool_get_big_orders,
    tool_get_kline,
    tool_get_market_snapshot,
    tool_get_master_contract,
    tool_get_open_interest_series,
    tool_get_orderbook,
    tool_get_quote,
)

logger = logging.getLogger("qihuo.ai.tools_registry")


async def load_skill_tools(session: AsyncSession, user_id: str) -> list[ToolDef]:
    """加载用户已启用的 skill 工具描述。"""
    from app.services.skill_manager import get_enabled_skills

    skills = await get_enabled_skills(session, UUID(user_id))
    default_names = {tool.name for tool in get_tool_definitions()}
    skill_tools: list[ToolDef] = []

    for skill in skills:
        tool_name = f"skill_{skill.trigger_word}"
        if skill.trigger_word in default_names:
            tool_name = f"{skill.trigger_word}_skill"

        skill_tools.append(
            ToolDef(
                name=tool_name,
                description=(
                    skill.description[:200]
                    if skill.description
                    else f"调用 {skill.name} skill"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "用户的具体请求",
                        },
                    },
                    "required": ["prompt"],
                },
            )
        )
    return skill_tools


def _to_json(payload: Any) -> str:
    """统一 JSON 字符串输出。"""
    return json.dumps(payload, ensure_ascii=False, default=str)


async def execute_tool(
    name: str,
    arguments: dict[str, Any],
    session: AsyncSession | None,
    user_id: str | None,
    redis_conn: Any | None,
    indicator_config: dict[str, Any] | None = None,
    execution_context: ExecutionContext | None = None,
) -> str:
    """执行工具调用，返回结果字符串。"""
    if requires_server_confirmation(name):
        logger.warning("request_id=%s user_id=%s tool=%s error_code=server_confirmation_required", execution_context.request_id if execution_context else "missing", user_id, name)
        return _to_json(CONFIRMATION_UNAVAILABLE)
    if execution_context is not None:
        if str(execution_context.user_id) != user_id or execution_context.market_type != "futures":
            return _to_json({"success": False, "error_code": "execution_context_mismatch", "error": "工具不适用于当前用户或市场，未执行"})
        if execution_context.trading_mode == "virtual" and (name in MODE_UNAWARE_TOOLS or name.startswith("skill_") or name.endswith("_skill")):
            return _to_json({"success": False, "error_code": "market_scope_unavailable", "error": "该工具尚未完成虚拟行情分盘适配，未读取其他盘行情"})
    # 用户在工具箱关闭的内置工具：直接拒绝
    if user_id and not name.startswith("skill_") and not name.endswith("_skill"):
        from app.services.ai.tools.preferences import is_tool_enabled

        if not await is_tool_enabled(redis_conn, user_id, name):
            return _to_json(
                {
                    "success": False,
                    "error": f"工具「{name}」已在设置中关闭，无法使用",
                }
            )

    if name.startswith("skill_") or name.endswith("_skill"):
        return await _execute_skill_tool(name, session, user_id)

    if name == "get_quote":
        result = await tool_get_quote(str(arguments.get("symbol", "")), redis_conn, trading_mode=execution_context.trading_mode if execution_context else "live")
        return _to_json(result)

    if name == "get_kline":
        limit_raw = arguments.get("limit")
        limit_value = int(limit_raw) if limit_raw is not None else None
        result = await tool_get_kline(
            str(arguments.get("symbol", "")),
            str(arguments.get("period", "5m")),
            limit_value,
            redis_conn,
            trading_mode=execution_context.trading_mode if execution_context else "live",
        )
        return _to_json(result)

    if name == "get_open_interest_series":
        limit_raw = arguments.get("limit")
        limit_value = int(limit_raw) if limit_raw is not None else None
        period = str(arguments.get("period") or "1d")
        result = await tool_get_open_interest_series(
            str(arguments.get("symbol", "")),
            period,
            limit_value,
            redis_conn,
        )
        return _to_json(result)

    if name == "get_master_contract":
        result = await tool_get_master_contract(
            str(arguments.get("code", "")),
            redis_conn,
        )
        return _to_json(result)

    if name == "get_market_snapshot":
        limit_raw = arguments.get("kline_limit")
        limit_value = int(limit_raw) if limit_raw is not None else None
        result = await tool_get_market_snapshot(
            str(arguments.get("symbol", "")),
            str(arguments.get("period") or "5m"),
            limit_value,
            redis_conn,
        )
        return _to_json(result)

    if name == "get_news":
        return await _execute_get_news(str(arguments.get("symbol", "")))

    if name == "get_big_orders":
        min_volume_raw = arguments.get("min_volume")
        limit_raw = arguments.get("limit")
        try:
            min_volume = int(min_volume_raw) if min_volume_raw is not None else None
            limit_value = int(limit_raw) if limit_raw is not None else None
        except (TypeError, ValueError):
            min_volume, limit_value = None, None
        result = await tool_get_big_orders(
            session,
            str(arguments.get("symbol", "")),
            min_volume,
            limit_value,
        )
        return _to_json(result)

    if name == "get_orderbook":
        result = await tool_get_orderbook(
            redis_conn,
            str(arguments.get("symbol", "")),
        )
        return _to_json(result)

    if name == "calculate_indicator":
        params = arguments.get("params")
        params_dict = params if isinstance(params, dict) else None
        lookback_raw = arguments.get("lookback")
        recent_raw = arguments.get("recent")
        lookback = int(lookback_raw) if lookback_raw is not None else None
        recent = int(recent_raw) if recent_raw is not None else None
        result = await tool_calculate_indicator(
            str(arguments.get("symbol", "")),
            str(arguments.get("indicator", "")),
            params_dict,
            str(arguments.get("period") or "5m"),
            lookback,
            recent,
            redis_conn,
            trading_mode=execution_context.trading_mode if execution_context else "live",
        )
        return _to_json(result)

    if name == "get_indicator_settings":
        return _to_json(tool_get_indicator_settings(indicator_config))

    if name == "update_indicator_settings":
        return _to_json(
            tool_update_indicator_settings(arguments, indicator_config)
        )

    if name == "code_sandbox":
        return await _execute_code_sandbox(str(arguments.get("code", "")))

    if name == "research_products":
        from app.services.ai.research import research_products_sync

        return await research_products_sync(arguments, redis_conn)

    from app.services.ai.tools.paper.dispatch import (
        dispatch_paper_tool,
        is_paper_tool,
    )

    if is_paper_tool(name):
        result = await dispatch_paper_tool(
            name, arguments, session, user_id, redis_conn, execution_context=execution_context
        )
        if result is not None:
            return result

    return _to_json({"success": False, "error": f"工具 {name} 不可用"})


async def _execute_get_news(symbol: str) -> str:
    """新闻工具。"""
    from app.data.contract_keywords import CONTRACT_KEYWORDS
    from app.data.contracts import get_code_for_symbol
    from app.data.news_fetcher import fetch_news_by_keyword

    symbol_l = symbol.lower().strip()
    code = get_code_for_symbol(symbol_l).lower() if symbol_l else ""
    if not code:
        code = "".join(ch for ch in symbol_l if ch.isalpha())
    keywords = CONTRACT_KEYWORDS.get(code, [])
    keyword = keywords[0] if keywords else (code or symbol_l or "期货")
    news_items = await fetch_news_by_keyword(keyword, 10)
    payload = [item.model_dump() for item in news_items[:10]]
    if not payload:
        return _to_json(
            {"success": False, "error": "未获取到新闻数据", "keyword": keyword}
        )
    return _to_json({"success": True, "keyword": keyword, "items": payload})


async def _execute_skill_tool(
    name: str,
    session: AsyncSession | None,
    user_id: str | None,
) -> str:
    """执行 skill 工具：读取 SKILL.md 全文返回给 AI。"""
    if not session or not user_id:
        return _to_json({"error": "skill 工具需要数据库会话", "success": False})

    from app.services.skill_manager import get_enabled_skills, read_skill_md

    trigger = name.removeprefix("skill_").removesuffix("_skill")
    skills = await get_enabled_skills(session, UUID(user_id))
    for skill in skills:
        if skill.trigger_word == trigger:
            content = read_skill_md(skill.skill_md_path)
            if content:
                return content
            return _to_json(
                {
                    "error": f"SKILL.md 文件不存在: {skill.name}",
                    "success": False,
                }
            )
    return _to_json(
        {"error": f"skill {name} 未找到或未启用", "success": False}
    )


async def _execute_code_sandbox(code: str) -> str:
    """在 Docker 沙箱中执行 Python 代码。"""
    try:
        import docker  # type: ignore

        client = docker.from_env()
        container = client.containers.run(
            image="python:3.12-slim",
            command=["python", "-c", code],
            detach=False,
            timeout=30,
            mem_limit="128m",
            network_disabled=True,
            read_only=True,
            stdout=True,
            stderr=True,
            remove=True,
        )
        output = container.decode("utf-8", errors="replace")
        return _to_json({"stdout": output[:3000], "success": True})
    except ImportError:
        return _to_json(
            {"error": "Docker 未安装，代码沙箱不可用", "success": False}
        )
    except Exception as exc:
        return _to_json(
            {"error": f"沙箱执行失败: {str(exc)[:200]}", "success": False}
        )


# 再导出定义 API，保持 tools 包统一入口
__all__ = [
    "execute_tool",
    "get_tool_definitions",
    "get_tool_map",
    "load_skill_tools",
]
