"""AI 交易模型非流式调用 —— 强制 JSON 决策"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_model import AIModel
from app.models.ai_provider import AIProvider
from app.models.ai_trading.task import AITradingTask
from app.services.ai_trading.core.capital import (
    apply_style_to_trade_rules,
    get_style_profile,
    horizon_label,
)
from app.services.ai_trading.llm.prompts import (
    build_system_prompt,
    build_user_prompt,
    parse_decision_text,
)
from app.services.crypto import decrypt
from app.services.ai.providers.urls import resolve_endpoint

logger = logging.getLogger("qihuo.ai_trading")

# 思考类模型名称启发式（capabilities 未标 thinking 时的兜底）：
# deepseek-*-thinking / deepseek-r1 / *-reasoner / o1/o3/o4-mini 等
_THINKING_NAME_RE = re.compile(
    r"thinking|reasoner|(^|[-_.])(r1|o1|o3|o4)([.\-]|$)", re.IGNORECASE
)
# 输出预算统一 32000：512 太小，模型先"口头推理"再写 JSON 时会被截断，
# 导致解析失败按观望处理（08-18 ta2609 实例）。上限仅是额度，正常 JSON 输出很短
_THINKING_MAX_TOKENS = 32000
_PLAIN_MAX_TOKENS = 32000
_RETRY_TOKENS_CAP = 8192
# 部分模型输出上限低于 32000，接口报 400 提到 max_tokens 时降档重试
_FALLBACK_MAX_TOKENS = 8192

# ===== 连续接口错误熔断 =====
# 08-17 事故：接口故障/限流时高频调用刷出 2000+ 条失败决策。连败 N 次进入冷却，
# 期间不再请求接口、直接降级观望；冷却结束或任一次成功后恢复。
_IFACE_FAIL_PREFIXES = ("模型异常", "模型HTTP", "ClaudeHTTP", "模型输出为空")
_BREAKER_FAILS_OPEN = 5
_BREAKER_COOLDOWN_S = 120
_breaker_fails = 0
_breaker_open_until = 0.0


def _note_interface_failure() -> None:
    global _breaker_fails, _breaker_open_until
    _breaker_fails += 1
    if _breaker_fails >= _BREAKER_FAILS_OPEN:
        _breaker_open_until = time.monotonic() + _BREAKER_COOLDOWN_S
        logger.warning(
            "模型接口连续失败 %d 次，熔断冷却 %ds", _breaker_fails, _BREAKER_COOLDOWN_S
        )


def _note_interface_ok() -> None:
    global _breaker_fails
    if _breaker_fails:
        logger.info("模型接口恢复（此前连败 %d 次）", _breaker_fails)
    _breaker_fails = 0


def _breaker_remaining_s() -> int:
    if _breaker_fails < _BREAKER_FAILS_OPEN:
        return 0
    remain = int(_breaker_open_until - time.monotonic())
    return remain if remain > 0 else 0


def _breaker_hold(remain_s: int) -> dict[str, Any]:
    return {
        "action": "hold",
        "quantity": 0,
        "reason": (
            f"模型接口熔断中（连续失败≥{_BREAKER_FAILS_OPEN}次，"
            f"{remain_s}s 后自动重试）"
        ),
        "confidence": 0,
        "raw": None,
        "parse_ok": False,
    }


def _is_thinking_model(model_id: str, caps: list[str]) -> bool:
    """是否按思考类模型处理：capabilities 显式标记或模型名命中启发式"""
    if "thinking" in caps:
        return True
    return bool(_THINKING_NAME_RE.search(str(model_id or "")))


def _decision_max_tokens(model_id: str, caps: list[str]) -> int:
    return _THINKING_MAX_TOKENS if _is_thinking_model(model_id, caps) else _PLAIN_MAX_TOKENS


async def call_model_decision(
    session: AsyncSession,
    task: AITradingTask,
    context: dict[str, Any],
) -> dict[str, Any]:
    """非流式调用模型拿 JSON 决策"""
    stmt = (
        select(AIModel, AIProvider)
        .join(AIProvider, AIModel.provider_id == AIProvider.id)
        .where(
            AIModel.id == task.model_row_id,
            AIModel.user_id == task.user_id,
        )
    )
    result = await session.execute(stmt)
    row = result.first()
    if row is None:
        return {
            "action": "hold",
            "quantity": 0,
            "reason": "模型不存在",
            "confidence": 0,
            "raw": None,
            "parse_ok": False,
        }
    model, provider = row
    api_key = decrypt(provider.api_key)
    risk_style = str(
        context.get("risk_style")
        or getattr(task, "risk_style", None)
        or "balanced"
    )
    custom_on = bool(
        context.get("custom_prompt_enabled")
        if context.get("custom_prompt_enabled") is not None
        else getattr(task, "custom_prompt_enabled", False)
    )
    base_hold = int(getattr(task, "max_hold_days", None) or 10)
    # 优先用引擎传入的生效规则；否则本地按风格缩放（用户提示词开启则风格失效）
    if context.get("close_rules") is not None and context.get("max_hold_days") is not None:
        eff_close = dict(context.get("close_rules") or {})
        eff_stop = dict(context.get("stop_rules") or {})
        eff_hold = int(context.get("max_hold_days") or base_hold)
        user_hold = int(context.get("user_max_hold_days") or base_hold)
        user_close = dict(
            context.get("user_close_rules")
            or getattr(task, "close_rules", None)
            or {}
        )
        user_stop = dict(
            context.get("user_stop_rules")
            or getattr(task, "stop_rules", None)
            or {}
        )
        profile = get_style_profile(risk_style, custom_on)
    else:
        styled = apply_style_to_trade_rules(
            risk_style=risk_style,
            close_rules=getattr(task, "close_rules", None) or {},
            stop_rules=getattr(task, "stop_rules", None) or {},
            max_hold_days=base_hold,
            custom_prompt_enabled=custom_on,
        )
        eff_close = {
            k: v for k, v in styled["close_rules"].items() if not str(k).startswith("_")
        }
        eff_stop = {
            k: v for k, v in styled["stop_rules"].items() if not str(k).startswith("_")
        }
        eff_hold = int(styled["max_hold_days"])
        user_hold = int(styled["user_max_hold_days"])
        user_close = dict(getattr(task, "close_rules", None) or {})
        user_stop = dict(getattr(task, "stop_rules", None) or {})
        profile = styled["profile"]
    allocated = float(
        context.get("allocated_capital")
        if context.get("allocated_capital") is not None
        else (getattr(task, "allocated_capital", None) or 0)
    )
    custom_text = getattr(task, "custom_prompt", None)
    system_prompt = build_system_prompt(
        risk_style=risk_style,
        timeframe=str(task.timeframe),
        max_hold_days=eff_hold,
        horizon=horizon_label(str(task.timeframe)),
        allocated_capital=allocated,
        custom_prompt_enabled=custom_on,
        custom_prompt=str(custom_text) if custom_text else None,
        user_max_hold_days=user_hold,
        style_hint=str(context.get("style_hint") or profile.get("prompt_hint") or ""),
        effective_close_rules=eff_close,
        effective_stop_rules=eff_stop,
        user_close_rules=user_close,
        user_stop_rules=user_stop,
        style_active=bool(profile.get("style_active", not custom_on)),
        has_factor_signal=bool(context.get("factor_signal")),
        has_quant_ref_signals=bool(context.get("quant_ref_signals")),
        extra_timeframes=[
            str(x)
            for x in (getattr(task, "extra_timeframes", None) or [])
            if x and x != str(task.timeframe)
        ][:3]
        or None,
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": build_user_prompt(context)},
    ]
    # 思考类模型需要更大的输出空间（思考 + JSON），避免思考吃满额度后 content 为空；
    # capabilities 未标记时按模型名兜底识别（deepseek-*-thinking / *-reasoner / r1 等）
    caps = (
        model.capabilities
        if isinstance(model.capabilities, list)
        else []
    )
    max_tokens = _decision_max_tokens(str(model.model_id or ""), caps)
    # 熔断冷却中：不打接口，直接降级观望（冷却结束自动恢复）
    breaker_remain = _breaker_remaining_s()
    if breaker_remain > 0:
        return _breaker_hold(breaker_remain)
    # Jev（System One 决策模型）无对话补全端点，不进熔断统计
    if provider.api_type == "jev":
        return {
            "action": "hold",
            "quantity": 0,
            "reason": "Jev 为决策模型，不支持对话调用，请换用对话模型",
            "confidence": 0,
            "raw": None,
            "parse_ok": False,
        }
    try:
        if provider.api_type == "claude":
            text = await _claude_once(
                provider.base_url, api_key, model.model_id, messages, max_tokens
            )
        else:
            text = await _openai_once(
                provider.base_url, api_key, model.model_id, messages, max_tokens
            )
    except Exception as exc:
        logger.exception("模型 HTTP 调用失败")
        _note_interface_failure()
        return {
            "action": "hold",
            "quantity": 0,
            "reason": f"模型异常: {exc}"[:80],
            "confidence": 0,
            "raw": None,
            "parse_ok": False,
        }

    parsed = parse_decision_text(text)
    # raw 仅审计，不进用户可读 reason
    parsed["raw"] = (text or "")[:2000]
    # 按结果更新熔断计数：接口类错误（异常/HTTP 非200/空输出）连败触发熔断；
    # 纯 JSON 解析失败（接口本身健康）不计入
    if str(parsed.get("reason") or "").startswith(_IFACE_FAIL_PREFIXES):
        _note_interface_failure()
    else:
        _note_interface_ok()
    if not parsed.get("parse_ok", True):
        logger.warning(
            "模型输出非严格JSON task=%s snippet=%s",
            task.id,
            (text or "")[:200],
        )
    return parsed


async def _post_openai_json(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
) -> tuple[str, str]:
    """单次 OpenAI 兼容请求，返回 (文本, finish_reason)。

    HTTP 非 200 时返回合成的 hold JSON 文本（保持既有降级语义），
    finish_reason 记为 "http_error"。
    """
    body_json = {**body, "response_format": {"type": "json_object"}}
    resp = await client.post(url, json=body_json, headers=headers)
    if resp.status_code == 400 and "response_format" in (resp.text or "").lower():
        resp = await client.post(url, json=body, headers=headers)
    if (
        resp.status_code == 400
        and "max_tokens" in (resp.text or "").lower()
        and int(body.get("max_tokens") or 0) > _FALLBACK_MAX_TOKENS
    ):
        # 模型输出上限低于统一预算：降档重试一次
        logger.warning(
            "OpenAI 决策 max_tokens=%s 超模型上限，降档 %d 重试",
            body.get("max_tokens"), _FALLBACK_MAX_TOKENS,
        )
        resp = await client.post(
            url, json={**body, "max_tokens": _FALLBACK_MAX_TOKENS}, headers=headers
        )
    if resp.status_code != 200:
        logger.warning("OpenAI 决策 HTTP %s: %s", resp.status_code, resp.text[:300])
        reason = (
            "模型HTTP429（限流/风控，请稍后重试或换模型）"
            if resp.status_code == 429
            else f"模型HTTP{resp.status_code}"
        )
        return (
            json.dumps(
                {
                    "action": "hold",
                    "quantity": 0,
                    "reason": reason,
                    "confidence": 0,
                },
                ensure_ascii=False,
            ),
            "http_error",
        )
    data = resp.json()
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    finish = str(choice.get("finish_reason") or "")
    content = str(msg.get("content") or "").strip()
    if not content:
        # 思考型兼容接口：正文为空时尝试从 reasoning_content 尾部抢救
        # （部分模型把最终 JSON 写在思考末尾；由 parse_decision_text 宽松提取）
        reasoning = str(msg.get("reasoning_content") or "").strip()
        if reasoning:
            content = reasoning[-2000:]
    return content, finish


async def _openai_once(
    base_url: str,
    api_key: str,
    model_id: str,
    messages: list[dict[str, Any]],
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
        "temperature": 0,
        # 尽量压低废话
        "max_tokens": max_tokens,
    }
    # 单次决策不宜过长，避免 AI 回测整体超时
    async with httpx.AsyncClient(timeout=45.0) as client:
        text, finish = await _post_openai_json(client, url, headers, body)
        if text:
            return text
        # content 为空：多为思考类模型思考吃满 max_tokens（finish=length），
        # 或未识别的思考模型。放大预算重试一次。
        if finish != "http_error":
            bigger = min(max_tokens * 4, _RETRY_TOKENS_CAP)
            if bigger > max_tokens:
                logger.warning(
                    "OpenAI 决策输出为空 model=%s finish=%s，以 max_tokens=%d 重试",
                    model_id, finish, bigger,
                )
                text, _ = await _post_openai_json(
                    client, url, headers, {**body, "max_tokens": bigger}
                )
                if text:
                    return text
        # 仍为空：给出可读原因，而非笼统的「模型未返回合法JSON」
        logger.warning("OpenAI 决策输出为空 model=%s finish=%s", model_id, finish)
        return json.dumps(
            {
                "action": "hold",
                "quantity": 0,
                "reason": "模型输出为空（思考被截断或接口风控）",
                "confidence": 0,
            },
            ensure_ascii=False,
        )


async def _claude_once(
    base_url: str,
    api_key: str,
    model_id: str,
    messages: list[dict[str, Any]],
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
    # 再次把「只输出 JSON」钉在 system 末尾
    system = (
        system
        + "\n\n再次确认：你的回复必须是单个 JSON 对象，禁止任何其它文字。"
    )
    body = {
        "model": model_id,
        "max_tokens": max_tokens,
        "temperature": 0,
        "system": system,
        "messages": claude_msgs,
    }
    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.post(url, json=body, headers=headers)
        if resp.status_code != 200:
            logger.warning("Claude 决策 HTTP %s: %s", resp.status_code, resp.text[:300])
            return json.dumps(
                {
                    "action": "hold",
                    "quantity": 0,
                    "reason": f"ClaudeHTTP{resp.status_code}",
                    "confidence": 0,
                },
                ensure_ascii=False,
            )
        data = resp.json()
        parts = data.get("content") or []
        texts = [p.get("text", "") for p in parts if p.get("type") == "text"]
        return "".join(texts)
