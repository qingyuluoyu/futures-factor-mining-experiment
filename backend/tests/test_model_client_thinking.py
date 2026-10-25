"""模型决策客户端单测 —— 思考型识别 / 空输出抢救与重试

线上问题：思考型模型（deepseek-*-thinking 等）capabilities 未标 thinking，
max_tokens=256 被思考过程吃满 → content 为空 → 「模型未返回合法JSON，已按观望处理」。
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app.services.ai_trading.llm.model_client import (
    _decision_max_tokens,
    _is_thinking_model,
    _openai_once,
)
from app.services.ai_trading.llm.prompts import parse_decision_text


# ===== 思考型识别 =====


def test_thinking_by_name_when_caps_missing() -> None:
    """capabilities 只有 tool_calling 时按名称识别思考型（线上实际数据形态）"""
    caps = ["tool_calling"]
    assert _is_thinking_model("deepseek-v4-flash-thinking", caps) is True
    assert _is_thinking_model("deepseek-v4-pro-thinking", caps) is True
    assert _is_thinking_model("deepseek-r1", caps) is True
    assert _is_thinking_model("qwen3.5-reasoner", caps) is True


def test_non_thinking_models() -> None:
    caps = ["tool_calling"]
    assert _is_thinking_model("qwen3.7-max", caps) is False
    assert _is_thinking_model("deepseek-v4-pro", caps) is False
    assert _is_thinking_model("gpt-4o-mini", caps) is False
    assert _is_thinking_model("claude-sonnet-4", caps) is False


def test_thinking_by_caps_flag() -> None:
    assert _is_thinking_model("whatever", ["thinking", "tool_calling"]) is True


def test_decision_max_tokens_budget() -> None:
    """输出预算统一 32000（原 512 会让先推理后写 JSON 的模型被截断）"""
    assert _decision_max_tokens("deepseek-v4-flash-thinking", []) == 32000
    assert _decision_max_tokens("qwen3.7-max", ["tool_calling"]) == 32000


# ===== _openai_once 行为 =====


class _Resp:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> dict:
        return self._payload or {}


class _FakeClient:
    """按顺序返回预置响应，记录请求体供断言"""

    def __init__(self, responses: list[_Resp]):
        self._responses = list(responses)
        self.bodies: list[dict] = []

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *args) -> bool:
        return False

    async def post(self, url: str, json: dict | None = None, headers=None) -> _Resp:
        self.bodies.append(json or {})
        return self._responses.pop(0)


def _ok(content: str, finish: str = "stop", reasoning: str = "") -> _Resp:
    return _Resp(
        200,
        {
            "choices": [
                {
                    "message": {"content": content, "reasoning_content": reasoning},
                    "finish_reason": finish,
                }
            ]
        },
    )


DECISION_JSON = json.dumps(
    {"action": "open_long", "quantity": 1, "reason": "突破", "confidence": 0.7},
    ensure_ascii=False,
)


@pytest.mark.asyncio
async def test_empty_content_with_length_finish_retries_bigger_budget() -> None:
    """content 空 + finish=length：放大 max_tokens 重试并采用第二次结果"""
    fake = _FakeClient(
        [
            _ok("", finish="length"),  # content/reasoning 均空 → 触发放大重试
            _ok(DECISION_JSON),
        ]
    )
    with patch(
        "app.services.ai_trading.llm.model_client.httpx.AsyncClient",
        return_value=fake,
    ):
        text = await _openai_once(
            "https://api.example.com", "sk-x", "deepseek-v4-flash-thinking",
            [{"role": "user", "content": "hi"}], 256,
        )
    assert json.loads(text)["action"] == "open_long"
    # 重试时放大了预算
    assert fake.bodies[0]["max_tokens"] == 256
    assert fake.bodies[1]["max_tokens"] == 1024


@pytest.mark.asyncio
async def test_reasoning_content_tail_rescued_without_retry() -> None:
    """content 空但 reasoning_content 尾部带 JSON：直接抢救，不再重试"""
    fake = _FakeClient(
        [
            _ok(
                "",
                finish="stop",
                reasoning="长篇思考过程…… 最终决定："
                + json.dumps(
                    {"action": "hold", "quantity": 0, "reason": "区间震荡", "confidence": 0.4}
                ),
            )
        ]
    )
    with patch(
        "app.services.ai_trading.llm.model_client.httpx.AsyncClient",
        return_value=fake,
    ):
        text = await _openai_once(
            "https://api.example.com", "sk-x", "some-model",
            [{"role": "user", "content": "hi"}], 512,
        )
    parsed = parse_decision_text(text)
    assert parsed["action"] == "hold"
    assert parsed["parse_ok"] is not False
    # 只发了一次请求（无重试）
    assert len(fake.bodies) == 1


@pytest.mark.asyncio
async def test_still_empty_returns_readable_reason() -> None:
    """两轮都空：返回可读原因（而非笼统「未返回合法JSON」）"""
    fake = _FakeClient([_ok("", finish="length"), _ok("", finish="length")])
    with patch(
        "app.services.ai_trading.llm.model_client.httpx.AsyncClient",
        return_value=fake,
    ):
        text = await _openai_once(
            "https://api.example.com", "sk-x", "m",
            [{"role": "user", "content": "hi"}], 8192,  # 已到上限不再放大
        )
    parsed = parse_decision_text(text)
    assert parsed["action"] == "hold"
    assert "模型输出为空" in parsed["reason"]


@pytest.mark.asyncio
async def test_http_429_synthesizes_hold_with_hint() -> None:
    """HTTP 429 风控：合成 hold 并提示换模型/稍后重试"""
    fake = _FakeClient([_Resp(429, text='{"error":"被挤爆啦"}')])
    with patch(
        "app.services.ai_trading.llm.model_client.httpx.AsyncClient",
        return_value=fake,
    ):
        text = await _openai_once(
            "https://api.example.com", "sk-x", "qwen3.7-max",
            [{"role": "user", "content": "hi"}], 512,
        )
    parsed = parse_decision_text(text)
    assert parsed["action"] == "hold"
    assert "429" in parsed["reason"]


@pytest.mark.asyncio
async def test_max_tokens_400_falls_back_to_smaller_budget() -> None:
    """模型输出上限低于统一预算：400 提到 max_tokens 时降档 8192 重试"""
    fake = _FakeClient(
        [
            _Resp(400, text='{"error":"max_tokens must be <= 16384"}'),
            _ok(DECISION_JSON),
        ]
    )
    with patch(
        "app.services.ai_trading.llm.model_client.httpx.AsyncClient",
        return_value=fake,
    ):
        text = await _openai_once(
            "https://api.example.com", "sk-x", "small-output-model",
            [{"role": "user", "content": "hi"}], 32000,
        )
    assert json.loads(text)["action"] == "open_long"
    assert fake.bodies[-1]["max_tokens"] == 8192


# ===== 熔断器 =====


def test_breaker_opens_after_consecutive_failures_and_resets() -> None:
    """连败 N 次进入冷却；任一次成功立即复位"""
    from app.services.ai_trading.llm import model_client as mc

    mc._breaker_fails = 0
    mc._breaker_open_until = 0.0
    try:
        for _ in range(mc._BREAKER_FAILS_OPEN):
            mc._note_interface_failure()
        assert mc._breaker_remaining_s() > 0
        mc._note_interface_ok()
        assert mc._breaker_fails == 0
        assert mc._breaker_remaining_s() == 0
    finally:
        mc._breaker_fails = 0
        mc._breaker_open_until = 0.0


def test_breaker_hold_payload_is_degraded_hold() -> None:
    from app.services.ai_trading.llm import model_client as mc

    out = mc._breaker_hold(37)
    assert out["action"] == "hold"
    assert out["parse_ok"] is False
    assert "熔断" in out["reason"]


# ===== 解析失败标记（前端「解析失败」徽标的数据来源） =====


def test_infer_from_text_marks_parse_fail() -> None:
    """关键词推断兜底：parse_ok=False（历史 08-18 ta2609 场景）"""
    parsed = parse_decision_text("我们需要输出仅一个JSON对象。综合看先观望。")
    assert parsed["parse_ok"] is False
    assert parsed["action"] == "hold"


def test_loose_fields_marks_parse_fail() -> None:
    """杂糅文本抓字段：parse_ok=False"""
    parsed = parse_decision_text(
        "分析后决定 action: hold, quantity: 0, reason: '震荡观望', confidence: 0.4"
    )
    assert parsed["parse_ok"] is False
    assert parsed["action"] == "hold"


def test_strict_json_still_parse_ok() -> None:
    """严格 JSON 输出不受影响"""
    parsed = parse_decision_text(DECISION_JSON)
    assert parsed["parse_ok"] is True
    assert parsed["action"] == "open_long"
