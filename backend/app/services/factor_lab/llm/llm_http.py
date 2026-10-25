"""因子实验室非流式 LLM 调用 —— 不依赖 AITradingTask"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_model import AIModel
from app.models.ai_provider import AIProvider
from app.services.crypto import decrypt
from app.services.ai.providers.urls import resolve_endpoint

logger = logging.getLogger("qihuo.factor_lab")


async def load_user_model(
    session: AsyncSession,
    user_id: UUID,
    model_row_id: UUID,
) -> tuple[AIModel, AIProvider] | None:
    """加载属于当前用户的模型+渠道；不存在返回 None"""
    stmt = (
        select(AIModel, AIProvider)
        .join(AIProvider, AIModel.provider_id == AIProvider.id)
        .where(
            AIModel.id == model_row_id,
            AIModel.user_id == user_id,
        )
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        return None
    return row[0], row[1]


async def chat_once(
    session: AsyncSession,
    user_id: UUID,
    model_row_id: UUID,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
) -> str:
    """非流式调用用户模型，返回文本；失败抛 ValueError"""
    loaded = await load_user_model(session, user_id, model_row_id)
    if loaded is None:
        raise ValueError("模型不存在或不属于当前用户")
    model, provider = loaded
    # Jev（System One 决策模型）无对话补全端点
    if provider.api_type == "jev":
        raise ValueError("Jev 为决策模型，不支持对话调用，请换用对话模型")
    api_key = decrypt(provider.api_key)
    if provider.api_type == "claude":
        return await _claude_once(
            provider.base_url,
            api_key,
            model.model_id,
            messages,
            temperature,
            max_tokens,
        )
    return await _openai_once(
        provider.base_url,
        api_key,
        model.model_id,
        messages,
        temperature,
        max_tokens,
    )


async def _openai_once(
    base_url: str,
    api_key: str,
    model_id: str,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
) -> str:
    url = resolve_endpoint(base_url, "/chat/completions")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body: dict[str, Any] = {
        "model": model_id,
        "messages": messages,
        "stream": False,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    body_json = {**body, "response_format": {"type": "json_object"}}
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.post(url, json=body_json, headers=headers)
        except httpx.TimeoutException:
            # 网关偶发抖动：超时单次重试（幂等的非流式补全）
            resp = await client.post(url, json=body_json, headers=headers)
        if resp.status_code == 400 and "response_format" in (resp.text or "").lower():
            resp = await client.post(url, json=body, headers=headers)
        if resp.status_code != 200:
            logger.warning("因子LLM OpenAI HTTP %s: %s", resp.status_code, resp.text[:300])
            raise ValueError(f"模型HTTP{resp.status_code}")
        data = resp.json()
        content = (
            data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
        )
        finish = data.get("choices", [{}])[0].get("finish_reason")
        # 推理模型（默认思考模式）会把 max_tokens 全部耗在 reasoning 上，
        # 正文 0 字符直接 length 截断——此时代码无法解析，快速报错引导换模型，
        # 避免三轮重试全部空转后静默返回 0 冠军
        if not content.strip() and finish == "length":
            raise ValueError(
                "模型输出被截断（推理模型耗尽 token 预算，正文为空）；"
                "请换非思考模式模型或提高 max_tokens"
            )
        return content


async def _claude_once(
    base_url: str,
    api_key: str,
    model_id: str,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
) -> str:
    url = resolve_endpoint(base_url, "/messages")
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    system = ""
    claude_msgs: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") == "system":
            system = str(msg.get("content") or "")
        else:
            claude_msgs.append(
                {"role": msg["role"], "content": msg.get("content") or ""}
            )
    system = system + "\n\n再次确认：只输出单个 JSON 对象，禁止其它文字。"
    body = {
        "model": model_id,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system,
        "messages": claude_msgs,
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, json=body, headers=headers)
        if resp.status_code != 200:
            logger.warning("因子LLM Claude HTTP %s: %s", resp.status_code, resp.text[:300])
            raise ValueError(f"ClaudeHTTP{resp.status_code}")
        data = resp.json()
        parts = data.get("content") or []
        texts = [p.get("text", "") for p in parts if p.get("type") == "text"]
        return "".join(texts)


def extract_json_object(text: str) -> dict[str, Any] | None:
    """从模型文本中提取 JSON 对象"""
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        return None
    return None
